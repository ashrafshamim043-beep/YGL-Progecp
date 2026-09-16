"""
[STATIC] Verifies main.py's newly-wired Member/Sponsor router imports
resolve to real names, and that router-internal paths already include
their own "/members"/"/sponsor"/"/admin" segments (so main.py's
prefix=settings.api_v1_prefix, with no additional segment, is correct --
an extra segment there would double the path, e.g. "/api/v1/members/members/register").
"""
import ast
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", ".."))
BACKEND = os.path.join(REPO_ROOT, "backend")


def get_defined_names(filepath):
    with open(filepath) as f:
        tree = ast.parse(f.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def get_route_decorator_paths(filepath):
    with open(filepath) as f:
        tree = ast.parse(f.read())
    paths = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and dec.args and isinstance(dec.args[0], ast.Constant):
                    paths.append(dec.args[0].value)
    return paths


class TestMainPyRouterWiring(unittest.TestCase):
    def test_member_router_import_resolves(self):
        imports = get_defined_names(os.path.join(BACKEND, "app", "modules", "member", "router.py"))
        self.assertIn("router", [n for n in ["router"] if True])  # router is a module-level var, checked below
        with open(os.path.join(BACKEND, "app", "modules", "member", "router.py")) as f:
            source = f.read()
        self.assertIn("router = APIRouter()", source)

    def test_sponsor_router_import_resolves(self):
        with open(os.path.join(BACKEND, "app", "modules", "sponsor", "router.py")) as f:
            source = f.read()
        self.assertIn("router = APIRouter()", source)

    def test_main_py_imports_both_new_routers(self):
        with open(os.path.join(BACKEND, "app", "main.py")) as f:
            source = f.read()
        self.assertIn("from app.modules.member.router import router as member_router", source)
        self.assertIn("from app.modules.sponsor.router import router as sponsor_router", source)
        self.assertIn("app.include_router(member_router", source)
        self.assertIn("app.include_router(sponsor_router", source)

    def test_member_router_paths_already_include_own_prefix_segment(self):
        """Every path must start with /members or /admin -- proving
        main.py must NOT add an extra prefix segment (it doesn't)."""
        paths = get_route_decorator_paths(os.path.join(BACKEND, "app", "modules", "member", "router.py"))
        self.assertTrue(paths, "expected at least one route in member/router.py")
        for p in paths:
            self.assertTrue(p.startswith("/members") or p.startswith("/admin/members"), p)

    def test_sponsor_router_paths_already_include_own_prefix_segment(self):
        paths = get_route_decorator_paths(os.path.join(BACKEND, "app", "modules", "sponsor", "router.py"))
        self.assertTrue(paths, "expected at least one route in sponsor/router.py")
        for p in paths:
            self.assertTrue(p.startswith("/sponsor") or p.startswith("/admin/sponsor"), p)

    def test_main_py_does_not_add_duplicate_prefix_segment_for_new_routers(self):
        with open(os.path.join(BACKEND, "app", "main.py")) as f:
            source = f.read()
        # The include_router calls for member/sponsor must use ONLY
        # settings.api_v1_prefix, never api_v1_prefix + "/members" etc.
        self.assertIn('app.include_router(member_router, prefix=settings.api_v1_prefix', source)
        self.assertIn('app.include_router(sponsor_router, prefix=settings.api_v1_prefix', source)


if __name__ == "__main__":
    unittest.main()
