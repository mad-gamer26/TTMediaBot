import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sdk  # noqa: E402,F401

from pydantic import BaseModel  # noqa: E402

from bot.manager import SERVER_NAME  # noqa: E402
from bot.config.models import LoggerModel, ServerModel  # noqa: E402
from bot.web import fields  # noqa: E402


def leaves(model, prefix=""):
    for name, info in model.model_fields.items():
        annotation = info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield from leaves(annotation, prefix + name + ".")
        else:
            yield prefix + name


class FormTest(unittest.TestCase):
    def test_every_server_setting_is_in_the_form(self):
        form = fields.server_form()
        shown = {field["path"] for section in form for field in section["fields"]}
        shown.add("enabled")  # shown as its own checkbox
        self.assertEqual(set(leaves(ServerModel)) - shown, set())

    def test_every_logger_setting_is_in_the_form(self):
        shown = {field["path"] for section in fields.logger_form() for field in section["fields"]}
        self.assertEqual(set(leaves(LoggerModel)) - shown, set())

    def test_secrets_use_password_fields(self):
        form = fields.server_form()
        kinds = {field["path"]: field["kind"] for section in form for field in section["fields"]}
        self.assertEqual(kinds["teamtalk.password"], "password")
        self.assertEqual(kinds["teamtalk.users.admins"], "lines")
        self.assertEqual(kinds["teamtalk.channel"], "int_or_text")


class ServerNameTest(unittest.TestCase):
    def test_names(self):
        for good in ("home", "Jason", "web-1", "a.b_c"):
            self.assertTrue(SERVER_NAME.match(good), good)
        for bad in ("", "-x", "two words", "x" * 41, "semi;colon", "../up"):
            self.assertFalse(SERVER_NAME.match(bad), bad)


class LoggerValidationTest(unittest.TestCase):
    def test_unknown_level_is_refused(self):
        self.assertEqual(LoggerModel(level="warning").level, "WARNING")
        with self.assertRaises(Exception):
            LoggerModel(level="LOUD")
        with self.assertRaises(Exception):
            LoggerModel(mode="SOMEWHERE")


if __name__ == "__main__":
    unittest.main()
