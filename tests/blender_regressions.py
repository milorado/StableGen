"""Offline, disposable Blender checks for the review fixes.

Inputs: factory scene, this checkout, optional STABLEGEN_TEST_DEPS directories.
Invariants: no live session, saved preferences, user files, backend or signals.
Outputs: assertions and a JSON summary. Unexpected exceptions exit nonzero with
--python-exit-code 1. Only temporary exports are written, then removed.
"""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
for directory in reversed(os.environ.get('STABLEGEN_TEST_DEPS', '').split(os.pathsep)):
    if directory:
        sys.path.insert(0, directory)
sys.path.insert(0, str(ROOT))

import bpy
import addon_utils
import PIL
import requests


def no_network(*_args, **_kwargs):
    raise AssertionError('Blender regression checks prohibit network access')


socket.create_connection = no_network
requests.sessions.Session.request = no_network
urllib.request.urlopen = no_network
module = addon_utils.enable('stablegen', default_set=True, persistent=False)
assert module is not None
assert tuple(map(int, PIL.__version__.split('.')[:2])) >= (13, 0)

from stablegen.cameras import visibility
from stablegen.texturing import rendering

scene = bpy.context.scene
cube = bpy.data.objects['Cube']
camera = bpy.data.objects['Camera']
camera.location, camera.rotation_euler = (0, 0, 5), (0, 0, 0)
bpy.context.view_layer.update()
first = visibility.detect(bpy.context, [camera], rays=64)[camera.name]
assert any(name == cube.name for name, share in first.shares)
for vertex in cube.data.vertices:
    vertex.co.x += 100
cube.data.update()
bpy.context.view_layer.update()
second = visibility.detect(bpy.context, [camera], rays=64)[camera.name]
assert second is not first
assert not any(name == cube.name for name, share in second.shares)

# A frame change must invalidate even if object/world transforms do not change.
scene.frame_set(scene.frame_current + 1)
assert visibility.cached(scene, camera, rays=64) is None

# Real native GLB exporter, unsafe object name and selection restoration.
bpy.ops.object.select_all(action='DESELECT')
cube.select_set(True)
bpy.context.view_layer.objects.active = cube
cube.name = '../asset'
with tempfile.TemporaryDirectory() as folder:
    bpy.context.preferences.addons['stablegen'].preferences.output_dir = folder
    with patch('stablegen.texturing.game_export.get_dir_path', return_value=folder):
        result = bpy.ops.object.export_game_engine('EXEC_DEFAULT')
    assert result == {'FINISHED'}, result
    files = list(Path(folder).glob('*.glb'))
    assert len(files) == 1 and files[0].stat().st_size > 0
    assert Path(folder, 'asset.glb').parent == files[0].parent
    assert bpy.context.view_layer.objects.active == cube and cube.select_get()

# Execute actual bake lifecycle with a synthetic disk-write failure. Run the
# deferred cleanup callback explicitly, so no event loop or renderer is needed.
callbacks = []
original_engine, original_samples = scene.render.engine, scene.cycles.samples
if not cube.data.materials:
    material = bpy.data.materials.new('Review fixture')
    material.use_nodes = True
    cube.data.materials.append(material)
with tempfile.TemporaryDirectory() as folder:
    bpy.context.preferences.addons['stablegen'].preferences.output_dir = folder
    with patch.object(rendering.bpy.app.timers, 'register', side_effect=lambda callback, **kw: callbacks.append(callback)), \
            patch.object(rendering, 'prepare_baking', side_effect=OSError('fixture disk full')):
        try:
            result = bpy.ops.object.bake_textures('EXEC_DEFAULT', bake_pbr=False)
        except RuntimeError as error:
            # bpy.ops converts a reported ERROR into a Python exception.
            assert 'fixture disk full' in str(error), error
            result = {'CANCELLED'}
assert result == {'CANCELLED'}
assert len(callbacks) == 1
callbacks.pop()()
assert scene.render.engine == original_engine
assert scene.cycles.samples == original_samples

handlers = (visibility._geometry_updated, visibility._frame_changed, visibility._file_loaded)
addon_utils.disable('stablegen', default_set=True)
assert not hasattr(bpy.types.Scene, 'architecture_mode')
assert handlers[0] not in bpy.app.handlers.depsgraph_update_post
assert handlers[1] not in bpy.app.handlers.frame_change_post
assert handlers[2] not in bpy.app.handlers.load_post
print(json.dumps({'blender': bpy.app.version_string, 'pillow': PIL.__version__,
                  'register_unregister': 'pass', 'geometry_and_frame_invalidation': 'pass',
                  'native_glb_export_and_containment': 'pass', 'bake_failure_cleanup': 'pass',
                  'network': 'prohibited'}))
