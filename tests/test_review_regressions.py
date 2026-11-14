import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from source_helpers import load_class_methods, load_functions, load_module


class ProcessOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process = load_module('setup.comfy_process')

    def test_recovery_and_later_pid_reuse(self):
        for recorded, live, folder_matches, should_stop in (
                ('original', 'original', True, True),
                ('original', 'different', True, False),
                ('original', 'original', False, False),
                (None, 'original', True, False),
                ('original', None, True, False)):
            with self.subTest(recorded=recorded, live=live, folder_matches=folder_matches):
                with tempfile.TemporaryDirectory() as folder:
                    state = Path(folder)
                    record = dict(pid=424242, port=8188, folder=str(state / 'comfy'), identity=recorded)
                    (state / 'comfyui.json').write_text(json.dumps(record))
                    manager = self.process.LocalComfyUI(state)
                    with patch.object(self.process, 'pid_alive', return_value=True), \
                            patch.object(self.process, 'process_identity', return_value=live), \
                            patch.object(self.process, 'server_folder', return_value=state / ('comfy' if folder_matches else 'other')), \
                            patch.object(self.process, 'stop_pid_tree') as stop:
                        manager.adopt_recorded()
                        self.assertEqual(manager.owned, should_stop)
                        manager.stop()
                        self.assertEqual(stop.called, should_stop)

    def test_identity_is_rechecked_at_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = self.process.LocalComfyUI(folder)
            manager.pid, manager._identity = 424242, 'old'
            with patch.object(self.process, 'pid_alive', return_value=True), \
                    patch.object(self.process, 'process_identity', return_value='reused'), \
                    patch.object(self.process, 'stop_pid_tree') as stop:
                manager.stop()
                stop.assert_not_called()

    def test_current_process_has_a_stable_identity(self):
        identity = self.process.process_identity(os.getpid())
        if identity is None:
            self.skipTest('OS sandbox blocks process introspection; ownership fails closed')
        self.assertEqual(identity, self.process.process_identity(os.getpid()))


class OutputPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = load_module('util.output_paths')

    def test_names_are_contained_and_disambiguated(self):
        with tempfile.TemporaryDirectory() as folder:
            names = ('asset.glb', '../asset.glb', '/tmp/asset.glb', 'C:\\asset.glb', '日本語.png', '..')
            results = [self.paths.safe_output_path(folder, name) for name in names]
            self.assertEqual(len(set(results)), len(names))
            self.assertEqual(results[0], os.path.join(os.path.realpath(folder), 'asset.glb'))
            for result in results:
                self.assertEqual(os.path.dirname(result), os.path.realpath(folder))

    def test_existing_symlink_cannot_overwrite_outside(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            Path(folder, 'asset.glb').symlink_to(Path(outside, 'asset.glb'))
            with self.assertRaises(ValueError):
                self.paths.safe_output_path(folder, 'asset.glb')


class ExportTests(unittest.TestCase):
    def test_native_export_status_and_output_are_both_required(self):
        paths = load_module('util.output_paths')
        for outcome in ('exception', 'cancelled', 'empty', 'missing', 'success'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as folder:
                mesh = NS(name='../asset', select_set=Mock())
                active = NS(active=mesh)
                context = NS(selected_objects=[mesh], view_layer=NS(objects=active))
                def export(**kwargs):
                    if outcome == 'exception':
                        raise OSError('fixture disk full')
                    if outcome == 'cancelled':
                        return {'CANCELLED'}
                    if outcome != 'missing':
                        Path(kwargs['filepath']).write_bytes(b'' if outcome == 'empty' else b'fixture glb')
                    return {'FINISHED'}
                bpy = NS(ops=NS(object=NS(select_all=Mock()), export_scene=NS(gltf=export)))
                env = dict(bpy=bpy, os=os, safe_output_path=paths.safe_output_path,
                           get_dir_path=lambda *a: folder,
                           _collect_game_export_objects=lambda *a: ([mesh], [], [mesh]))
                cls = load_class_methods('stablegen/texturing/game_export.py', 'ExportForGameEngine', {'execute'}, env)
                operator = cls()
                operator.export_scope, operator.export_format = 'SELECTED', 'GLB'
                operator.apply_transforms, operator.export_animations = False, False
                operator.report = Mock()
                result = operator.execute(context)
                self.assertEqual(result, {'FINISHED'} if outcome == 'success' else {'CANCELLED'})
                self.assertEqual(active.active, mesh)
                self.assertEqual(mesh.select_set.call_args.args, (True,))

    def test_batch_partial_file_is_never_promoted_on_failure(self):
        for outcome in ('exception', 'cancelled', 'empty', 'missing', 'success'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as folder:
                destination = Path(folder, 'asset.glb')
                destination.write_bytes(b'earlier valid asset')
                Path(str(destination) + '.partial.glb').write_bytes(b'stale partial file')
                def export(**kwargs):
                    if outcome != 'missing':
                        Path(kwargs['filepath']).write_bytes(b'' if outcome == 'empty' else b'new asset')
                    if outcome == 'exception':
                        raise OSError('disk full')
                    return {'CANCELLED'} if outcome == 'cancelled' else {'FINISHED'}
                bpy = NS(context=NS(scene=NS(objects=[]), selected_objects=[]),
                         ops=NS(object=NS(select_all=Mock()), export_scene=NS(gltf=export)))
                state = dict(out_dir=folder, files=[dict(output_relative_path='asset.glb')], index=0)
                env = dict(os=os, bpy=bpy, _batch_state=state)
                load_functions('stablegen/dae_import/batch_texture.py', {'_do_export'}, env)
                if outcome == 'success':
                    env['_do_export']()
                    self.assertEqual(destination.read_bytes(), b'new asset')
                else:
                    with self.assertRaises((OSError, RuntimeError)):
                        env['_do_export']()
                    self.assertEqual(destination.read_bytes(), b'earlier valid asset')
                self.assertFalse(Path(str(destination) + '.partial.glb').exists())

    def test_batch_export_failure_enters_retry_policy(self):
        generator = ModuleType('review_source.texturing.generator')
        generator.ComfyUIGenerate = NS(_is_running=False)
        retry = Mock(return_value=.5)
        env = dict(__package__='review_source.dae_import',
                   _batch_state=dict(active=True, cancelled=False, phase='export'),
                   _do_export=Mock(side_effect=OSError('disk full')), _retry_or_skip=retry,
                   _TICK_INTERVAL=.5)
        load_functions('stablegen/dae_import/batch_texture.py', {'_batch_tex_tick'}, env)
        with patch.dict(sys.modules, {'review_source.texturing.generator': generator}):
            env['_batch_tex_tick']()
        self.assertEqual(retry.call_args.args[0], 'export')


class BatchDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_module('dae_import.batch_manifest')

    def test_repeat_run_excludes_prior_outputs_but_preserves_other_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'textured').mkdir()
            (root / 'other').mkdir()
            for name in ('input.glb', 'textured/input.glb', 'other/second.glb'):
                (root / name).touch()
            entries = self.manifest.discover_model_files(folder, exclude_dirs=(root / 'textured',))
            self.assertEqual([entry['relative_path'] for entry in entries], ['input.glb', 'other/second.glb'])
            self.assertEqual(entries, self.manifest.discover_model_files(folder, exclude_dirs=(root / 'textured',)))

    def test_output_alias_and_same_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'results').mkdir()
            (root / 'results/result.glb').touch()
            (root / 'alias.glb').symlink_to(root / 'results/result.glb')
            self.assertEqual(self.manifest.discover_model_files(folder, exclude_dirs=(root / 'results',)), [])
            with self.assertRaises(ValueError):
                self.manifest.discover_model_files(folder, exclude_dirs=(root,))

    def test_output_on_another_drive_does_not_hide_inputs(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as output:
            Path(folder, 'input.glb').touch()
            with patch.object(self.manifest.os.path, 'commonpath', side_effect=ValueError('different drives')):
                entries = self.manifest.discover_model_files(folder, exclude_dirs=(output,))
            self.assertEqual([entry['relative_path'] for entry in entries], ['input.glb'])


class BakeCleanupTests(unittest.TestCase):
    def test_failure_cancel_and_success_restore_state_once(self):
        for outcome in ('exception', 'cancel', 'finished'):
            with self.subTest(outcome=outcome):
                callbacks = []
                timers = NS(register=lambda callback, **kw: callbacks.append(callback))
                wm = NS(event_timer_remove=Mock())
                scene = NS(render=NS(engine='CYCLES', use_persistent_data=True),
                           cycles=NS(device='GPU', samples=32, max_bounces=8, use_denoising=False))
                shading1, shading2 = NS(type='SOLID'), NS(type='SOLID')
                context = NS(window_manager=wm, scene=scene, preferences=NS(addons={}), view_layer=NS(update=Mock()))
                env = dict(bpy=NS(app=NS(timers=timers)))
                cls = load_class_methods('stablegen/texturing/rendering.py', 'BakeTextures',
                                         {'modal', 'cancel', '_cleanup_bake'}, env)
                operator = cls()
                operator.report = Mock()
                operator._timer = 'timer'
                operator._bake_snapshot = (scene, 'BLENDER_EEVEE_NEXT', 'CPU', 99, 12, False, True, None,
                                           [(shading1, 'RENDERED'), (shading2, 'MATERIAL')])
                if outcome == 'exception':
                    operator._modal_bake = Mock(side_effect=OSError('disk full'))
                    self.assertEqual(operator.modal(context, NS(type='TIMER')), {'CANCELLED'})
                elif outcome == 'cancel':
                    self.assertEqual(operator.modal(context, NS(type='ESC')), {'CANCELLED'})
                else:
                    operator._cleanup_bake(context)
                operator.cancel(context)
                wm.event_timer_remove.assert_called_once_with('timer')
                self.assertEqual(len(callbacks), 1)
                callbacks[0]()
                self.assertEqual(scene.render.engine, 'BLENDER_EEVEE_NEXT')
                self.assertEqual(scene.cycles.samples, 99)
                self.assertEqual((shading1.type, shading2.type), ('RENDERED', 'MATERIAL'))


class SharedServerTests(unittest.TestCase):
    def test_external_server_receives_no_global_maintenance(self):
        runtime = ModuleType('review_source.setup.runtime')
        runtime.owns_server = lambda address: False
        env = dict(__package__='review_source')
        cls = load_class_methods('stablegen/workflows.py', '_WorkflowBase', {'_flush_comfyui_vram'}, env)
        operator = cls()
        operator._get_vram_stats = Mock(side_effect=AssertionError('Must not touch external server'))
        with patch.dict(sys.modules, {'review_source.setup.runtime': runtime}):
            self.assertFalse(operator._flush_comfyui_vram('remote:8188'))

    def test_only_owned_matching_server_can_be_maintained(self):
        env = dict(_comfy=NS(proc=NS(poll=lambda: None), address='127.0.0.1:8188'))
        load_functions('stablegen/setup/runtime.py', {'owns_server'}, env)
        self.assertTrue(env['owns_server']('127.0.0.1:8188'))
        self.assertFalse(env['owns_server']('remote:8188'))
        env['_comfy'].proc = None
        self.assertFalse(env['owns_server']('127.0.0.1:8188'))
