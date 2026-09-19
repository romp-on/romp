"""A restored view keeps its hydration source when another leaf of the same session restores."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from tests.test_asm_checkpoint import Harness, SID, NOW, compacting_variant, em, G


class LazySourceLifetime(Harness):
    def make_document(self, directory, fsid, tag, section=True):
        parent = self.td / directory
        parent.mkdir()
        path = parent / (fsid + ".jsonl")
        records = compacting_variant([
            G.uline(G.T0, "prompt for " + tag, "user-" + tag),
            G.aline(G.T0 + 5, "answer for " + tag, "assistant-" + tag, "user-" + tag),
        ], tag)
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        tree = self.parse_leaf(path)
        self.assertTrue(em.asm_checkpoint_write(str(path), SID, tree=tree if section else None))
        return path

    def parse_leaf(self, path, modes=None):
        return em.parse_session(str(path), rompuuid=SID, name="web", dir="/TESTDIR",
                                candidate_files=[str(path)], postal_log=[], now=NOW, asm_mode_out=modes)

    def restore(self, path):
        modes = []
        tree = self.parse_leaf(path, modes)
        self.assertEqual(modes, ["restore"])
        return tree

    @staticmethod
    def user_atom(tree, tag):
        return next(a for t in tree["turns"] for a in t["atoms"] if a.get("uuid") == "user-" + tag)

    def test_another_leaf_does_not_remove_a_held_views_source(self):
        first = self.make_document("web", G.FSID_A, "web")
        second = self.make_document("api", G.FSID_B, "api")
        self.fresh()
        old = self.restore(first)
        atom = self.user_atom(old, "web")
        self.assertIsNotNone(atom.get("lazy"))
        self.restore(second)
        self.assertTrue(first.exists())
        em.hydrate([atom])
        self.assertEqual(atom["message"]["content"], [{"type": "text", "text": "prompt for web"}])

    def test_same_fsid_in_another_location_does_not_retarget_a_held_view(self):
        first = self.make_document("web", G.FSID_A, "web")
        second = self.make_document("api", G.FSID_A, "api")
        self.fresh()
        atom = self.user_atom(self.restore(first), "web")
        self.restore(second)
        em.hydrate([atom])
        self.assertEqual(atom["message"]["content"], [{"type": "text", "text": "prompt for web"}])

    def test_atoms_only_checkpoint_keeps_its_source_too(self):
        first = self.make_document("web", G.FSID_A, "web", section=False)
        second = self.make_document("api", G.FSID_B, "api")
        self.fresh()
        atom = self.user_atom(self.restore(first), "web")
        self.restore(second)
        copied = dict(atom)
        em.hydrate([copied])
        self.assertEqual(copied["message"]["content"], [{"type": "text", "text": "prompt for web"}])

    def test_materializing_after_another_restore_uses_the_original_document(self):
        first = self.make_document("web", G.FSID_A, "web")
        second = self.make_document("api", G.FSID_B, "api")
        self.fresh()
        old = self.restore(first)
        self.restore(second)
        atom = self.user_atom(old, "web")
        em.hydrate([atom])
        self.assertEqual(atom["message"]["content"], [{"type": "text", "text": "prompt for web"}])

    def test_concurrent_restores_hydrate_their_own_views(self):
        first = self.make_document("web", G.FSID_A, "web")
        second = self.make_document("api", G.FSID_B, "api")
        self.fresh()
        barrier = threading.Barrier(2)

        def run(path, tag):
            tree = self.restore(path)
            barrier.wait(timeout=5)
            atom = self.user_atom(tree, tag)
            em.hydrate([atom])
            return atom["message"]["content"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(run, first, "web")
            b = pool.submit(run, second, "api")
            self.assertEqual(a.result(timeout=10), [{"type": "text", "text": "prompt for web"}])
            self.assertEqual(b.result(timeout=10), [{"type": "text", "text": "prompt for api"}])

    def test_a_removed_source_does_not_redirect_to_another_document(self):
        first = self.make_document("web", G.FSID_A, "web")
        second = self.make_document("api", G.FSID_A, "api")
        self.fresh()
        atom = self.user_atom(self.restore(first), "web")
        self.restore(second)
        first.unlink()
        with self.assertRaises(FileNotFoundError):
            em.hydrate([atom])
