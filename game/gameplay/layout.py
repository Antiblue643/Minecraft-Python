

def options_screen_layout(width, height, option_count, has_mod_settings=False):
    width = max(1, int(width))
    height = max(1, int(height))
    option_count = max(0, int(option_count))
    rows = (option_count + 1) // 2

    horizontal_space = max(1, width - 10)
    column_gap = min(10, max(2, horizontal_space // 12))
    column_width = min(150, max(1, (horizontal_space - column_gap) // 2))
    grid_width = 2 * column_width + column_gap
    left_x = (width - grid_width) // 2
    right_x = left_x + column_width + column_gap

    row_height, top, gap, done_gap = 24, height // 6, 12, 36
    required_bottom = top + row_height * (rows + 1) + gap + done_gap + 20 + 8
    compact = required_bottom > height
    if compact:
        row_height, gap, done_gap = 22, 6, 24
        content_height = row_height * (rows + 1) + gap + done_gap + 20
        top = max(30, min(36, height - content_height - 8))

    option_rects = []
    for index in range(option_count):
        x = left_x if index % 2 == 0 else right_x
        y = top + row_height * (index // 2)
        option_rects.append((x, y, column_width, 20))

    utility_y = top + row_height * rows
    username_rect = (left_x, utility_y, column_width, 20)
    mod_settings_rect = (
        (right_x, utility_y, column_width, 20) if has_mod_settings else None
    )

    controls_width = min(310, horizontal_space)
    done_width = min(200, controls_width)
    controls_y = utility_y + row_height + gap
    controls_rect = ((width - controls_width) // 2, controls_y, controls_width, 20)
    done_rect = (
        (width - done_width) // 2, controls_y + done_gap, done_width, 20,
    )

    return {
        "option_rects": tuple(option_rects),
        "username_rect": username_rect,
        "mod_settings_rect": mod_settings_rect,
        "controls_rect": controls_rect,
        "done_rect": done_rect,
        "compact": compact,
        "column_gap": column_gap,
        "column_width": column_width,
    }
