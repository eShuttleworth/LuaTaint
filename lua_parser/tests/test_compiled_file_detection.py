import os
import tempfile
from core.ast_helper import is_compiled_lua
from lua_parser.utils import tests


class CompiledFileDetectionTest(tests.TestCase):
    def test_detect_compiled_lua(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix='.lua') as f:
            f.write(b'\x1bLuaQ')
            path = f.name
        try:
            self.assertTrue(is_compiled_lua(path))
        finally:
            os.unlink(path)

    def test_source_file_not_compiled(self):
        with tempfile.NamedTemporaryFile('w', delete=False, suffix='.lua') as f:
            f.write('print("hello")')
            path = f.name
        try:
            self.assertFalse(is_compiled_lua(path))
        finally:
            os.unlink(path)
