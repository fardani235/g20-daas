from pydantic import BaseModel

from app.analysis import registry
from app.analysis.registry import AnalysisOp, all_ops, catalog, get_op, register


class _DummyParams(BaseModel):
    foo: int = 1


def _dummy_handler(inputs, params, output_path):
    return {"output_path": output_path, "metadata": {}}


def _dummy_op():
    return AnalysisOp(
        op_id="dummy-test-op",
        label="Dummy",
        description="test op",
        version="0.0.1",
        params_model=_DummyParams,
        output_kind="raster",
        render_kind="dem",
        inputs=[{"name": "raster", "datasets": ["dsm"]}],
        handler=_dummy_handler,
    )


def test_register_dummy_op_and_list_it():
    op = _dummy_op()
    register(op)
    try:
        assert get_op("dummy-test-op") is op
        assert op in all_ops()
        entry = op.catalog_entry()
        assert entry["op_id"] == "dummy-test-op"
        assert entry["output_kind"] == "raster"
        assert entry["render_kind"] == "dem"
        assert entry["inputs"] == [{"name": "raster", "datasets": ["dsm"]}]
        assert entry["params_schema"]["properties"]["foo"]["type"] == "integer"
    finally:
        del registry._REGISTRY["dummy-test-op"]


def test_duplicate_registration_rejected():
    op = _dummy_op()
    register(op)
    try:
        try:
            register(_dummy_op())
        except ValueError as e:
            assert "already registered" in str(e)
        else:
            raise AssertionError("expected duplicate registration to fail")
    finally:
        del registry._REGISTRY["dummy-test-op"]


def test_catalog_schema_version():
    assert catalog()["schema_version"] == registry.CATALOG_SCHEMA_VERSION
