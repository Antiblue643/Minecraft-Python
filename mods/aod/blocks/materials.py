

def register_materials():
    from game.block import material as block_material

    tnt_material = block_material.Material(
        "tnt",
        block_material.StepSound("grass", 1.0, 1.0),
    )
    block_material.MATERIALS["tnt"] = tnt_material

    materials_by_name = {
        "corruptor_tnt": tnt_material,
        "kinoko": tnt_material,
        "photobomb": tnt_material,
        "fallout": block_material.SNOW,
        "photon_lamp": block_material.GLASS,
        "photon_lamp_lit": block_material.GLASS,
        "tesla": block_material.METAL,
        "magnifying_glass": block_material.GLASS,
    }
    block_material.BLOCK_MATERIALS.update(materials_by_name)
    return materials_by_name
