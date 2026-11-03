"""Static polygon/RenderCore bridge for Houdini 20.5 and unmodified CarXBridge Max 2023."""
from .bridge import create_material, export_selection, export_nodes, material_data
from .importer import import_from_max
__version__ = '1.2.0'
