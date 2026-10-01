"""Bounded image-header inspection. No decoding, new dependencies or arbitrary image proxy."""
import struct


def dimensions(body):
    if body.startswith(b'\x89PNG\r\n\x1a\n') and len(body) >= 33 and body[12:16] == b'IHDR':
        if struct.unpack('>I', body[8:12])[0] != 13:
            raise ValueError('Invalid PNG header')
        width, height = struct.unpack('>II', body[16:24])
    elif body.startswith(b'\xff\xd8'):
        pos = 2
        width = height = 0
        while pos < len(body):
            if body[pos] != 255:
                raise ValueError('Invalid JPEG marker')
            while pos < len(body) and body[pos] == 255:
                pos += 1
            if pos >= len(body): break
            marker = body[pos]; pos += 1
            if marker in (0xD9, 0xDA): break
            if marker == 0x01 or 0xD0 <= marker <= 0xD8: continue
            if pos + 2 > len(body): break
            length = struct.unpack('>H', body[pos:pos+2])[0]
            if length < 2 or pos + length > len(body): break
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                if length < 8: break
                height, width = struct.unpack('>HH', body[pos+3:pos+7])
                break
            pos += length
    else:
        raise ValueError('Unsupported image')
    if not (16 <= width <= 12000 and 16 <= height <= 12000 and width * height <= 40_000_000):
        raise ValueError('Invalid image dimensions')
    return width, height


def role(width, height, hint='poster'):
    ratio = width / height
    if .55 <= ratio <= .8:
        return 'poster'
    if 1.4 <= ratio <= 2.1:
        return 'episode_still' if hint == 'episode_still' else 'backdrop'
    return None
