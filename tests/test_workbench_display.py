"""Display boundaries are not manufacturing or prescription validity verdicts."""
from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.workbench_display import display_layout
from core.workbench_snapshot import build_layout


def model():
    def row(i, radius=0., thickness=2., material=''):
        return dict(index=i, type='Standard', radius=radius, conic=0., thickness=thickness,
                    semi_diameter=5., is_stop=i==1, material=material)
    return {'units':'mm','system_mode':'Sequential', 'surfaces':[
        row(0,thickness=math.inf),row(1,20.,4.,'N-BK7'),row(2,-20.,30.),row(3,thickness=math.inf)]}


def native_sag(m, i, y):
    r=m['surfaces'][i]['radius']
    # Independent primary sphere sag with the observed native saturation.
    return (True, math.copysign(abs(r)-math.sqrt(max(0.,r*r-y*y)),r) if r else 0., 0.)


class DisplayTests(unittest.TestCase):
    def display(self, m, **overrides):
        drawing={'mechanical':6.,'hidden':False,'edges':'Squared','no_edges':False, **overrides}
        return display_layout(m,lambda i:drawing,lambda i,y:native_sag(m,i,y))

    def test_native_sag_surface_and_mechanical_lands_preserve_prescription(self):
        m=model();before=deepcopy(m);view=self.display(m)
        self.assertEqual(view['status'],'ready');self.assertEqual(m,before)
        front=view['surfaces'][0];self.assertEqual(front['points'][0][1],-6.)
        self.assertEqual(view['symmetric_planes'],['YZ','XZ'])
        self.assertAlmostEqual(front['points'][1][0],20-math.sqrt(375))
        self.assertEqual(front['points'][0][0],front['points'][1][0])
        self.assertEqual(front['points'][-1][1],6.)
        self.assertEqual([s['vertex_z'] for s in view['surfaces']],[0.,4.,34.])
        self.assertEqual(view['bounds'][1::2],[-6.,6.])
        self.assertEqual(m['surfaces'][1]['semi_diameter'],5.)

    def test_out_of_domain_native_display_does_not_relabel_invalid_prescription(self):
        m=model();m['surfaces'][1]['radius']=-1.82
        before=deepcopy(m);view=self.display(m)
        self.assertEqual(build_layout(m)['status'],'unsupported')
        self.assertEqual(view['status'],'ready');self.assertEqual(m,before)
        points=view['surfaces'][0]['points']
        self.assertIn([-1.82,-1.82],points);self.assertIn([-1.82,1.82],points)
        self.assertEqual(points[0],[-1.82,-6.])
        self.assertTrue(all(math.isfinite(v) for p in points for v in p))

    def test_material_extending_to_image_is_a_display_region_with_explicit_identity(self):
        m=model();m['surfaces'][2]['material']='1.33,55.8'
        self.assertEqual(build_layout(m)['status'],'unsupported')
        view=self.display(m);self.assertEqual(view['status'],'ready')
        region=view['elements'][-1]
        self.assertTrue(region['ends_at_image'])
        self.assertEqual((region['front'],region['back'],region['material']),(2,3,'1.33,55.8'))

    def test_sag_failure_never_leaves_partial_contours(self):
        for raw in ((False,0.,0.),(True,float('nan'),0.),(True,)):
            view=display_layout(model(),lambda i:{'mechanical':6.,'hidden':False,'edges':'Squared','no_edges':False},lambda i,y:raw)
            self.assertEqual(view['status'],'unsupported');self.assertEqual(view['surfaces'],[])
            self.assertEqual(view['elements'],[]);self.assertTrue(view['issues'])

    def test_unknown_coordinates_rejected_before_native_sag(self):
        forbidden=Mock(side_effect=AssertionError('must not sample a guessed axial frame'))
        for change in ({'layout_issues':['tilted frame']},{'system_mode':'NonSequential'}):
            m={**model(),**change}
            self.assertEqual(display_layout(m,forbidden,forbidden)['status'],'unsupported')
        m=model();m['surfaces'][1]['type']='CoordinateBreak'
        self.assertEqual(display_layout(m,forbidden,forbidden)['status'],'unsupported')
        forbidden.assert_not_called()

    def test_unsupported_drawing_flags_and_invalid_apertures_are_explicit(self):
        for change in ({'hidden':True},{'edges':'Slanted'},{'no_edges':True},{'mechanical':4.},{'mechanical':math.inf}):
            with self.subTest(change=change):
                view=self.display(model(),**change)
                self.assertEqual(view['status'],'unsupported');self.assertEqual(view['surfaces'],[])

    def test_native_property_failure_drops_only_the_display(self):
        m=model();before=deepcopy(m)
        def unavailable(i):raise RuntimeError('native property unavailable')
        view=display_layout(m,unavailable,lambda i,y:native_sag(m,i,y))
        self.assertEqual(view['status'],'unsupported');self.assertEqual(m,before)
        self.assertEqual(view['surfaces'],[]);self.assertIn('native property unavailable',view['issues'][0])

    def test_sampling_budget_and_surface_limit_are_bounded(self):
        with patch('core.workbench_display.time.monotonic',side_effect=[0.,6.,6.]):
            view=self.display(model())
        self.assertEqual(view['status'],'unsupported');self.assertEqual(view['surfaces'],[])
        self.assertIn('5 秒',view['issues'][0])
        m=model();m['surfaces']=m['surfaces']*60
        forbidden=Mock(side_effect=AssertionError('over-limit model must not call native'))
        view=display_layout(m,forbidden,forbidden)
        self.assertEqual(view['status'],'unsupported');self.assertIn('200 面',view['issues'][0])
        forbidden.assert_not_called()

    def test_repeated_display_observations_do_not_change_state_for_timing(self):
        with patch('core.workbench_display.time.monotonic',return_value=1.):
            first=self.display(model())
        with patch('core.workbench_display.time.monotonic',return_value=3.):
            second=self.display(model())
        self.assertEqual(first,second)


if __name__=='__main__':unittest.main()
