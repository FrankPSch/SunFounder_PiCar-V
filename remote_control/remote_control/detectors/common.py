'''What the detectors share.'''


def box_target(x, y, width, height, frame_width, frame_height, label):
    return {'x': (x + width / 2) / frame_width, 'y': (y + height / 2) / frame_height,
            'w': width / frame_width, 'h': height / frame_height, 'size': width / frame_width, 'label': label}
