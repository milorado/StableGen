"""Load focused Blender functions without importing or mutating a live scene."""
import ast
import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def load_functions(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text())
    functions = [node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(functions) == len(names), (path, names)
    for node in functions:
        node.decorator_list = []
    exec(compile(ast.Module(body=functions, type_ignores=[]), path, 'exec'), namespace)
    return namespace


def load_module(path):
    parts = path.split('.')
    for index in range(len(parts)):
        name = '.'.join(['review_source', *parts[:index]])
        if name not in sys.modules:
            package = types.ModuleType(name)
            package.__path__ = [str(ROOT / 'stablegen' / Path(*parts[:index]))]
            sys.modules[name] = package
    name = '.'.join(['review_source', *parts])
    spec = importlib.util.spec_from_file_location(name, ROOT / 'stablegen' / Path(*parts).with_suffix('.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_class_methods(path, class_name, names, namespace):
    tree = ast.parse((ROOT / path).read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    methods = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(methods) == len(names), (path, names)
    for node in methods:
        node.decorator_list = []
    exec(compile(ast.Module(body=methods, type_ignores=[]), path, 'exec'), namespace)
    return type(class_name, (), {name: namespace[name] for name in names})
