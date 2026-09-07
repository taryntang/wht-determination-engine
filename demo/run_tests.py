"""Run all Python tests without third-party dependencies; pytest also works."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    suite = unittest.TestSuite()
    for path in sorted((ROOT / 'tests').glob('test_*.py')):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
        for name, value in vars(module).items():
            if name.startswith('test_') and callable(value):
                suite.addTest(unittest.FunctionTestCase(value))
    return not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()

if __name__ == '__main__':
    sys.exit(main())
