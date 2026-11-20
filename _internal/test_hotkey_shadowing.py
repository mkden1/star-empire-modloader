"""Guard: no keyboard branch in Client.py is unreachable behind an earlier branch for the same key.

The client reads its one-shot hotkeys from long ``if/elif event.key == pygame.K_x`` chains. When a vanilla
update adds a hotkey, the merge is textually clean even if a mod already uses that key further down the
chain - the later branch just stops running (v0.5.74: the dev's L = Skills silently killed our L = Combat
Log). This test walks every such chain and fails on a branch that an earlier, unconditional branch for the
same key always wins first. A branch with extra conditions (``K_l and shift``) does not shadow what follows.
"""
import ast
import unittest

import modloader


def _keys_of(test):
    """``(keys, conditional)`` for an ``event.key`` test, else None.

    ``keys`` is the tuple of ``K_*`` names the branch fires on; ``conditional`` is True when the branch has
    more conditions than the key (so it can decline and let a later branch run)."""
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        head = _keys_of(test.values[0])
        return (head[0], True) if head else None
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return None
    left = test.left
    if not (isinstance(left, ast.Attribute) and left.attr == "key"
            and isinstance(left.value, ast.Name) and left.value.id == "event"):
        return None
    names = []
    right = test.comparators[0]
    if isinstance(test.ops[0], ast.Eq):
        candidates = [right]
    elif isinstance(test.ops[0], ast.In) and isinstance(right, (ast.Tuple, ast.List, ast.Set)):
        candidates = right.elts
    else:
        return None
    for node in candidates:
        if not (isinstance(node, ast.Attribute) and node.attr.startswith("K_")
                and isinstance(node.value, ast.Name) and node.value.id == "pygame"):
            return None
        names.append(node.attr)
    return tuple(names), False


def shadowed_branches(source):
    """``[(line, key, line_of_the_branch_that_wins)]`` for every unreachable ``event.key`` branch."""
    tree = ast.parse(source)
    elif_children = {id(node.orelse[0]) for node in ast.walk(tree)
                     if isinstance(node, ast.If) and len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If)}
    found = []
    for head in ast.walk(tree):
        if not isinstance(head, ast.If) or id(head) in elif_children:
            continue
        decided = {}          # key name -> line of the first unconditional branch for it
        node = head
        while True:
            parsed = _keys_of(node.test)
            if parsed:
                keys, conditional = parsed
                for key in keys:
                    if key in decided:
                        found.append((node.lineno, key, decided[key]))
                    elif not conditional:
                        decided[key] = node.lineno
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                node = node.orelse[0]
            else:
                break
    return found


class CheckerTests(unittest.TestCase):
    def test_a_repeated_plain_key_is_flagged(self):
        src = ("if event.key == pygame.K_a:\n    pass\n"
               "elif event.key == pygame.K_l:\n    pass\n"
               "elif event.key == pygame.K_b:\n    pass\n"
               "elif event.key == pygame.K_l:\n    pass\n")
        self.assertEqual(shadowed_branches(src), [(7, "K_l", 3)])

    def test_a_conditioned_branch_ahead_of_the_plain_one_is_fine(self):
        src = ("if event.key == pygame.K_l and (event.mod & pygame.KMOD_SHIFT):\n    pass\n"
               "elif event.key == pygame.K_l:\n    pass\n")
        self.assertEqual(shadowed_branches(src), [])

    def test_a_conditioned_branch_behind_the_plain_one_is_flagged(self):
        src = ("if event.key == pygame.K_l:\n    pass\n"
               "elif event.key == pygame.K_l and (event.mod & pygame.KMOD_SHIFT):\n    pass\n")
        self.assertEqual(shadowed_branches(src), [(3, "K_l", 1)])

    def test_a_key_inside_a_tuple_counts(self):
        src = ("if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):\n    pass\n"
               "elif event.key == pygame.K_KP_ENTER:\n    pass\n")
        self.assertEqual(shadowed_branches(src), [(3, "K_KP_ENTER", 1)])

    def test_separate_chains_do_not_interfere(self):
        src = ("if event.key == pygame.K_a:\n    pass\n"
               "if event.key == pygame.K_a:\n    pass\n")
        self.assertEqual(shadowed_branches(src), [])

    def test_other_tests_in_the_chain_are_ignored(self):
        src = ("if event.key == pygame.K_a:\n    pass\n"
               "elif something_else:\n    pass\n"
               "elif event.key == pygame.K_a:\n    pass\n")
        self.assertEqual(shadowed_branches(src), [(5, "K_a", 1)])


class ClientHotkeyTests(unittest.TestCase):
    def test_no_hotkey_branch_in_client_py_is_dead(self):
        found = shadowed_branches(modloader.game_source("Client.py"))      # the patched text: ours + the dev's
        self.assertEqual(
            found, [],
            "unreachable keyboard branches in Client.py (line, key, earlier branch that wins) - an update "
            "probably gave a vanilla feature a key one of our mods already uses; move ours to another key "
            "or a modifier:\n" + "\n".join(f"  line {a}: {k} is shadowed by line {b}" for a, k, b in found))


if __name__ == "__main__":
    unittest.main()
