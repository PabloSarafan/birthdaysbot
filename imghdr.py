"""
Compatibility shim for imghdr module (removed in Python 3.13)
Provides minimal functionality needed by python-telegram-bot
"""

def what(file, h=None):
    """
    Simple image type detection using file signatures
    """
    if h is None:
        if isinstance(file, str):
            with open(file, 'rb') as f:
                h = f.read(32)
        else:
            location = file.tell()
            h = file.read(32)
            file.seek(location)
    
    # Check common image signatures
    if h[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    elif h[:3] == b'\xff\xd8\xff':
        return 'jpeg'
    elif h[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    elif h[:4] == b'RIFF' and h[8:12] == b'WEBP':
        return 'webp'
    elif h[:2] == b'BM':
        return 'bmp'
    
    return None

