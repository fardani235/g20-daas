"""Minimal JSON-Schema validation for plugin settings.

The geospatial service emits parameter schemas via pydantic's
``model_json_schema()``. The operations expose flat objects of scalars, so a
small validator for that subset is enough and avoids adding a dependency to the
Frappe image. Anything the schemas do not use ($ref, allOf, ...) is ignored
rather than mis-validated.
"""

_TYPES = {
    "number": (int, float),
    "integer": int,
    "string": str,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class SchemaValidationError(ValueError):
    """Raised when a value does not conform to a plugin's parameter schema."""


def _matches_type(value, type_name) -> bool:
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    expected = _TYPES.get(type_name)
    if expected is None:
        return True
    return isinstance(value, expected)


def _validate_value(name, value, spec):
    type_name = spec.get("type")
    if type_name and not _matches_type(value, type_name):
        raise SchemaValidationError(f"'{name}' must be of type {type_name}")

    if "enum" in spec and value not in spec["enum"]:
        raise SchemaValidationError(f"'{name}' must be one of {spec['enum']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in spec and value < spec["minimum"]:
            raise SchemaValidationError(f"'{name}' must be >= {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            raise SchemaValidationError(f"'{name}' must be <= {spec['maximum']}")
        if "exclusiveMinimum" in spec and value <= spec["exclusiveMinimum"]:
            raise SchemaValidationError(f"'{name}' must be > {spec['exclusiveMinimum']}")
        if "exclusiveMaximum" in spec and value >= spec["exclusiveMaximum"]:
            raise SchemaValidationError(f"'{name}' must be < {spec['exclusiveMaximum']}")

    if isinstance(value, str):
        if "minLength" in spec and len(value) < spec["minLength"]:
            raise SchemaValidationError(f"'{name}' is too short")
        if "maxLength" in spec and len(value) > spec["maxLength"]:
            raise SchemaValidationError(f"'{name}' is too long")


def validate(settings, schema):
    """Validate ``settings`` against a JSON schema object.

    A missing/empty schema accepts any object. Unknown properties are rejected
    so typos surface instead of being silently stored.
    """
    if not schema:
        if settings not in (None, {}):
            if not isinstance(settings, dict):
                raise SchemaValidationError("settings must be an object")
        return

    if not isinstance(settings, dict):
        raise SchemaValidationError("settings must be an object")

    if schema.get("type") not in (None, "object"):
        raise SchemaValidationError("settings schema must describe an object")

    properties = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in settings:
            raise SchemaValidationError(f"missing required parameter: {key}")

    for key, value in settings.items():
        if key not in properties:
            raise SchemaValidationError(f"unknown parameter: {key}")
        _validate_value(key, value, properties[key])
