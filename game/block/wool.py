

def wool_texture_index(metadata: int, definition: dict) -> int:
    meta = int(metadata) & 15
    variants = definition.get("metaFaces") if isinstance(definition, dict) else None
    if isinstance(variants, dict):
        try:
            return int(variants.get(str(meta), definition.get("faces", 0)))
        except (TypeError, ValueError, OverflowError):
            pass
    try:
        return int(definition.get("faces", 0))
    except (AttributeError, TypeError, ValueError, OverflowError):
        return 0
