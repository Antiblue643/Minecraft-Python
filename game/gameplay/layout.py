

def _button_pair_geometry(width):
    width = max(1, int(width))
    horizontal_space = max(1, width - 10)
    column_gap = min(10, max(2, horizontal_space // 12))
    column_width = min(150, max(1, (horizontal_space - column_gap) // 2))
    grid_width = 2 * column_width + column_gap
    left_x = (width - grid_width) // 2
    right_x = left_x + column_width + column_gap
    return left_x, right_x, column_width


def options_screen_layout(width, height, option_count, has_mod_settings=False):
    width = max(1, int(width))
    height = max(1, int(height))
    option_count = max(0, int(option_count))
    rows = (option_count + 1) // 2
    left_x, right_x, column_width = _button_pair_geometry(width)
    center_button_width = min(200, max(1, width - 20))

    compact = height < 280
    row_step = 20 if compact else 24
    if compact:
        if rows <= 3:
            footer_bottom = 188
        else:
            footer_bottom = rows * row_step + 92
        top = max(8, min(height // 6, height - footer_bottom - 4))
    else:
        relative_bottom = rows * row_step + 116
        if relative_bottom > height - 4:
            compact = True
            row_step = 20
            if rows <= 3:
                footer_bottom = 188
            else:
                footer_bottom = rows * row_step + 92
            top = max(8, min(height // 6, height - footer_bottom - 4))
        else:
            top = max(8, min(height // 6, height - relative_bottom - 4))

    option_rects = []
    for index in range(option_count):
        x = left_x if index % 2 == 0 else right_x
        y = top + row_step * (index // 2)
        option_rects.append((x, y, column_width, 20))

    utility_y = top + rows * row_step
    username_rect = (left_x, utility_y, column_width, 20)

    mod_settings_rect = None
    if has_mod_settings:
        if option_count % 2 == 1 and rows > 0:
            mod_y = top + (rows - 1) * row_step
        else:
            mod_y = utility_y
        mod_settings_rect = (right_x, mod_y, column_width, 20)

    if compact and rows <= 3:
        video_y = top + 108
        controls_y = top + 132
        done_y = top + 168
    elif compact:
        video_y = utility_y + 24
        controls_y = video_y + 24
        done_y = controls_y + 24
    else:
        video_y = utility_y + 36
        controls_y = video_y + 24
        done_y = controls_y + 36

    video_rect = ((width - center_button_width) // 2, video_y,
                  center_button_width, 20)
    controls_rect = ((width - center_button_width) // 2, controls_y,
                     center_button_width, 20)
    done_rect = ((width - center_button_width) // 2, done_y,
                 center_button_width, 20)

    return {
        "option_rects": tuple(option_rects),
        "username_rect": username_rect,
        "mod_settings_rect": mod_settings_rect,
        "video_rect": video_rect,
        "controls_rect": controls_rect,
        "done_rect": done_rect,
        "compact": compact,
        "column_width": column_width,
    }


def video_settings_screen_layout(width, height, option_count):
    width = max(1, int(width))
    height = max(1, int(height))
    option_count = max(0, int(option_count))
    left_x, right_x, column_width = _button_pair_geometry(width)
    rows = (option_count + 1) // 2
    compact = height < 280
    row_step = 20 if compact else 24
    relative_bottom = rows * row_step + 188
    if relative_bottom > height - 4:
        compact = True
        row_step = 20
    top = max(8, min(height // 6, height - (rows * row_step + 44) - 4))
    option_rects = tuple(
        (
            left_x if index % 2 == 0 else right_x,
            top + row_step * (index // 2),
            column_width,
            20,
        )
        for index in range(option_count)
    )
    done_y = min(max(top + rows * row_step + 24, top + 168), height - 24)
    done_width = min(200, max(1, width - 20))
    done_rect = ((width - done_width) // 2, done_y, done_width, 20)
    return {
        "option_rects": option_rects,
        "done_rect": done_rect,
        "column_width": column_width,
    }


def create_world_layout(width, height):
    width = max(1, int(width))
    height = max(1, int(height))
    field_width = min(200, max(1, width - 20))
    field_x = (width - field_width) // 2

    if height < 300:
        button_width = min(150, max(1, (width - 22) // 2))
        pair_gap = 8
        pair_width = button_width * 2 + pair_gap
        left_x = (width - pair_width) // 2
        right_x = left_x + button_width + pair_gap
        first_button_y = max(140, height - 52)
        second_button_y = first_button_y + 24
        return {
            "compact": True,
            "title_y": 8,
            "field_x": field_x,
            "field_width": field_width,
            "name_label_y": 20,
            "name_field_y": 32,
            "folder_y": 56,
            "seed_label_y": 74,
            "seed_field_y": 86,
            "seed_info_y": 110,
            "error_y": 128,
            "create_rect": (left_x, first_button_y, button_width, 20),
            "cancel_rect": (right_x, first_button_y, button_width, 20),
            "generation_rect": (left_x, second_button_y, button_width, 20),
            "mode_rect": (right_x, second_button_y, button_width, 20),
        }

    base_y = height // 4
    left_button_x = width // 2 - 100
    return {
        "compact": False,
        "title_y": height // 4 - 40,
        "field_x": field_x,
        "field_width": field_width,
        "name_label_y": 47,
        "name_field_y": 60,
        "folder_y": 85,
        "seed_label_y": 104,
        "seed_field_y": 116,
        "seed_info_y": 140,
        "error_y": 160,
        "create_rect": (left_button_x, base_y + 108, 200, 20),
        "cancel_rect": (left_button_x, base_y + 132, 200, 20),
        "generation_rect": (left_button_x, base_y + 156, 200, 20),
        "mode_rect": (left_button_x, base_y + 180, 200, 20),
    }
