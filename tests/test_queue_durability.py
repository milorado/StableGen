import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from source_helpers import load_class_methods, load_functions


class QueueDurabilityTests(unittest.TestCase):
    def environment(self, outcome):
        events = []
        item = NS(status='processing', scene_name='Generated', error_reason='')
        queue = [item, NS(status='pending')]
        def save():
            events.append('save')
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        bpy = NS(data=NS(filepath='current.blend', is_dirty=True),
                 context=NS(window_manager=NS(sg_scene_queue=queue, sg_queue_gif_export=False)),
                 ops=NS(wm=NS(save_mainfile=save, open_mainfile=lambda **kw: events.append('open'))))
        env = dict(bpy=bpy, os=os, _queue_current_idx=0, _queue_phase='running',
                   _queue_processing=True, _queue_timer='timer', _queue_force_reload=False,
                   _persist_queue=lambda: events.append('persist') or True, _tag_redraw=lambda: None)
        names = {'_queue_finish_item', '_queue_save_and_advance', '_queue_tick'}
        # Include save helpers when running the fixed implementation.
        import ast
        from source_helpers import ROOT
        tree = ast.parse((ROOT / 'stablegen/ui/queue.py').read_text())
        names |= {node.name for node in tree.body if isinstance(node, ast.FunctionDef)
                  and (node.name.startswith('_queue_save_current') or node.name == '_queue_has_save_error')}
        load_functions('stablegen/ui/queue.py', names, env)
        return env, item, events

    def test_failed_result_save_does_not_complete_or_advance(self):
        for outcome in (OSError('disk full'), {'CANCELLED'}):
            with self.subTest(outcome=outcome):
                env, item, events = self.environment(outcome)
                env['_queue_finish_item'](item)
                self.assertNotEqual(item.status, 'done')
                self.assertEqual(env['_queue_current_idx'], 0)
                self.assertFalse(env['_queue_processing'])
                self.assertNotIn('open', events)

    def test_successful_save_completes_before_advancing(self):
        env, item, events = self.environment({'FINISHED'})
        env['_queue_finish_item'](item)
        self.assertEqual(item.status, 'done')
        self.assertEqual(env['_queue_current_idx'], 1)
        self.assertLess(events.index('save'), events.index('persist'))

    def test_failed_save_before_switch_keeps_current_scene(self):
        for outcome in (OSError('read only'), {'CANCELLED'}):
            env, item, events = self.environment(outcome)
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / 'next.blend'
                target.touch()
                item.blend_file = str(target)
                env['_queue_phase'] = 'idle'
                env['_queue_tick']()
            self.assertNotIn('open', events)
            self.assertFalse(env['_queue_processing'])

    def test_untitled_result_cannot_be_marked_done(self):
        env, item, events = self.environment({'FINISHED'})
        env['bpy'].data.filepath = ''
        env['bpy'].data.is_dirty = False
        env['_queue_finish_item'](item)
        self.assertNotEqual(item.status, 'done')
        self.assertFalse(env['_queue_processing'])

    def test_retrying_result_save_does_not_repeat_generation(self):
        env, item, events = self.environment(OSError('disk full'))
        env['_queue_finish_item'](item)
        self.assertIsNone(env['_queue_tick']())
        self.assertEqual(env['_queue_phase'], 'save_failed_result')
        env['bpy'].ops.wm.save_mainfile = lambda: {'FINISHED'}
        register = Mock()
        env['bpy'].app = NS(timers=NS(register=register))
        cls = load_class_methods('stablegen/ui/queue.py', 'SceneQueueProcess', {'execute'}, env)
        operator = cls()
        operator.report = Mock()
        operator.execute(env['bpy'].context)
        self.assertEqual(item.status, 'done')
        self.assertEqual(env['_queue_current_idx'], 1)
        register.assert_called_once()

    def test_queue_persistence_failure_prevents_file_switch(self):
        env, item, events = self.environment({'FINISHED'})
        env['_persist_queue'] = lambda: False
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'next.blend'
            target.touch()
            item.blend_file = str(target)
            env['_queue_phase'] = 'idle'
            env['_queue_tick']()
        self.assertNotIn('open', events)
        self.assertFalse(env['_queue_processing'])

    def test_retrying_last_item_reports_success_without_starting_a_timer(self):
        env, item, events = self.environment(OSError('disk full'))
        env['bpy'].context.window_manager.sg_scene_queue = [item]
        env['_queue_finish_item'](item)
        env['bpy'].ops.wm.save_mainfile = lambda: {'FINISHED'}
        register = Mock()
        env['bpy'].app = NS(timers=NS(register=register))
        cls = load_class_methods('stablegen/ui/queue.py', 'SceneQueueProcess', {'execute'}, env)
        operator = cls()
        operator.report = Mock()
        self.assertEqual(operator.execute(env['bpy'].context), {'FINISHED'})
        self.assertEqual(item.status, 'done')
        self.assertFalse(env['_queue_processing'])
        register.assert_not_called()


if __name__ == '__main__':
    unittest.main()
