# memos-toolset tests

The plugin folder name contains a hyphen, which makes pytest's default
discovery walk fail (the plugin's own `__init__.py` is not a test module
but pytest tries to import it as one), so these tests are excluded from the
repo-root collection in `pytest.ini`. The local `pytest.ini` in this
directory anchors the collection tree here, so run from the repo root:

```bash
pytest deploy/plugins/memos-toolset/tests/test_auto_capture.py -v
```

No real MemOS server is needed — `_fake_server.FakeMemOSServer` stands in
on a free localhost port.
