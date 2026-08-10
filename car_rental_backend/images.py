"""Optimisation des images avant stockage (Supabase / disque).

Réduit la taille des fichiers pour économiser de l'espace et accélérer
l'affichage : correction de l'orientation EXIF, redimensionnement et
ré-encodage JPEG/PNG/WEBP.
"""

from io import BytesIO

from django.core.files.base import File

# Extensions traitées par Pillow. Tout le reste (PDF, SVG, HEIC...) est ignoré.
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp'}

DEFAULT_MAX_DIMENSION = 1600
DEFAULT_JPEG_QUALITY = 82


def is_optimizable_image(name, content=None):
    ext = str(name).lower()
    if any(ext.endswith(e) for e in IMAGE_EXTS):
        return True
    if content is not None:
        ctype = getattr(content, 'content_type', None) or ''
        return ctype.startswith('image/')
    return False


def _decode(content):
    """Lit les octets du fichier uploadé en revenant au début du flux."""
    try:
        content.seek(0)
    except (AttributeError, ValueError, OSError):
        pass
    if isinstance(content, File):
        return content.read()
    return content.getvalue()


def optimize_image_bytes(name, content, max_dimension=DEFAULT_MAX_DIMENSION, quality=DEFAULT_JPEG_QUALITY):
    """Re-encode l'image dans un flux optimisé, ou renvoie None si ignorable."""
    from PIL import Image, ImageOps

    if not is_optimizable_image(name, content):
        return None

    try:
        raw = _decode(content)
        img = Image.open(BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        img.load()
    except Exception:
        return None

    ext = str(name).lower()

    # Redimensionnement si l'image dépasse la taille maximale.
    if img.width > max_dimension or img.height > max_dimension:
        img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

    has_alpha = 'A' in img.getbands()

    if ext.endswith('.png'):
        if not has_alpha:
            img = img.convert('RGB')
        buffer = BytesIO()
        img.save(buffer, 'PNG', optimize=True)
        return buffer

    if ext.endswith('.webp'):
        buffer = BytesIO()
        img.save(buffer, 'WEBP', quality=quality, method=4)
        return buffer

    # JPEG (et autres formats raster) : aplatissement + ré-encodage JPEG.
    if has_alpha:
        background = Image.new('RGB', img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    else:
        img = img.convert('RGB')

    buffer = BytesIO()
    img.save(buffer, 'JPEG', quality=quality, optimize=True, progressive=True)
    return buffer
