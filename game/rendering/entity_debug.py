

def debug_hitbox_entities(world):
    if world is None:
        return []
    entities = list(getattr(world, "entities", ()) or ())
    player = getattr(world, "player", None)
    if (player is not None and not any(player is item for item in entities)
            and not getattr(player, "dead", False)):
        entities.append(player)
    for remote in getattr(world, "_mp_remote_players", ()) or ():
        if (remote is not None and getattr(remote, "should_render", True)
                and not any(remote is item for item in entities)):
            entities.append(remote)
    return entities


def debug_entity_id_tag_entries(
    world, camera_player=None, partial=1.0, *, include_player=False,
    extra_entities=(), max_distance=64.0,
):
    if world is None:
        return []

    candidates = list(debug_hitbox_entities(world))
    for entity in extra_entities or ():
        if entity is not None and not any(entity is item for item in candidates):
            candidates.append(entity)

    partial = max(0.0, min(1.0, float(partial)))
    if camera_player is not None:
        try:
            camera_pos = tuple(float(value) for value in camera_player.pos[:3])
        except (AttributeError, TypeError, ValueError, IndexError):
            camera_pos = None
    else:
        camera_pos = None

    max_distance_sq = max(0.0, float(max_distance)) ** 2
    entries = []
    for entity in candidates:
        if entity is None or bool(getattr(entity, "dead", False)):
            continue
        if camera_player is entity and not include_player:
            continue
        if not bool(getattr(entity, "should_render", True)):
            continue

        entity_id = getattr(entity, "entity_id", None)
        if entity_id is None:
            entity_id = getattr(entity, "_mp_net_id", None)
        try:
            entity_id = int(entity_id)
        except (TypeError, ValueError, OverflowError):
            continue
        if entity_id < 0:
            continue

        pos = getattr(entity, "pos", None)
        if pos is None:
            continue
        try:
            current = tuple(float(value) for value in pos[:3])
            previous = tuple(
                float(value) for value in getattr(entity, "prev_pos", current)[:3]
            )
        except (TypeError, ValueError, IndexError):
            continue
        if len(current) != 3 or len(previous) != 3:
            continue
        x, y, z = tuple(
            previous[index] + (current[index] - previous[index]) * partial
            for index in range(3)
        )
        if camera_pos is not None:
            dx, dy, dz = (x - camera_pos[0], y - camera_pos[1], z - camera_pos[2])
            if dx * dx + dy * dy + dz * dz > max_distance_sq:
                continue

        size = getattr(entity, "size", (0.6, 1.8, 0.6))
        try:
            height = max(0.1, float(size[1]))
        except (TypeError, ValueError, IndexError):
            height = 1.8
        y += height + 0.25 - float(getattr(entity, "render_y_offset", 0.0) or 0.0)
        entries.append((entity, entity_id, (x, y, z)))
    return entries


def draw_entity_id_tags(
    world, renderer, display, camera_player, partial=1.0, *,
    include_player=False, extra_entities=(), max_distance=64.0,
):
    entries = debug_entity_id_tag_entries(
        world, camera_player, partial, include_player=include_player,
        extra_entities=extra_entities, max_distance=max_distance,
    )
    if not entries or renderer is None or display is None:
        return 0

    try:
        from OpenGL.GL import (
            GL_ALL_ATTRIB_BITS, GL_BLEND, GL_CULL_FACE, GL_DEPTH_TEST, GL_LIGHTING,
            GL_ONE_MINUS_SRC_ALPHA, GL_QUADS, GL_SRC_ALPHA, GL_TEXTURE_2D,
            glBegin, glBlendFunc, glColor4f, glDisable, glEnable, glEnd,
            glPopAttrib, glPopMatrix, glPushAttrib, glPushMatrix, glRotatef,
            glScalef, glTranslatef, glVertex3f, glDepthMask,
        )
    except Exception:
        return 0

    camera_yaw = float(getattr(camera_player, "yaw", 0.0))
    camera_pitch = float(getattr(camera_player, "pitch", 0.0))
    rendered = 0
    glPushAttrib(GL_ALL_ATTRIB_BITS)
    try:
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_BLEND)
        glDisable(GL_LIGHTING)
        glDisable(GL_CULL_FACE)
        for _entity, entity_id, world_pos in entries:
            try:
                relative = renderer.camera_relative_offset(*world_pos)
            except Exception:
                relative = (
                    world_pos[0] - float(getattr(renderer, "camX", 0.0)),
                    world_pos[1] - float(getattr(renderer, "camY", 0.0)),
                    world_pos[2] - float(getattr(renderer, "camZ", 0.0)),
                )
            half_width = float(display.string_width(str(entity_id), scale=1.0)) * 0.5
            scale = (1.0 / 60.0) * 1.6
            glPushMatrix()
            try:
                glTranslatef(*relative)
                glRotatef(camera_yaw, 0.0, 1.0, 0.0)
                glRotatef(camera_pitch, 1.0, 0.0, 0.0)
                glScalef(scale, -scale, scale)

                glDepthMask(False)
                glDisable(GL_DEPTH_TEST)
                glEnable(GL_BLEND)
                glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                glDisable(GL_TEXTURE_2D)
                glColor4f(0.0, 0.0, 0.0, 0.25)
                glBegin(GL_QUADS)
                glVertex3f(-half_width - 1.0, -1.0, 0.0)
                glVertex3f(-half_width - 1.0, 8.0, 0.0)
                glVertex3f(half_width + 1.0, 8.0, 0.0)
                glVertex3f(half_width + 1.0, -1.0, 0.0)
                glEnd()

                display.draw_string(
                    str(entity_id), (-half_width, 0.0), (255, 255, 255),
                    scale=1.0, shadow=False, alpha=0.125,
                )
                glEnable(GL_DEPTH_TEST)
                glDepthMask(True)
                display.draw_string(
                    str(entity_id), (-half_width, 0.0), (255, 255, 255),
                    scale=1.0, shadow=False,
                )
                rendered += 1
            finally:
                glPopMatrix()
    finally:
        glPopAttrib()
    return rendered
