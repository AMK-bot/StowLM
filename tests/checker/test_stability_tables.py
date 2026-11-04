"""Table mechanics against a closed-form wall-sided box, not cached answers."""
from types import SimpleNamespace
import numpy as np
import pytest
from stowage.checker.stability_tables import (
    TableGeometry, OutsideTable, _basis, load_table_geometry,
)
from stowage.vessel.hydrostatics import HullGeometry, Submerged


class BoxGeometry:
    lpp_m=80.; beam_m=12.; depth_m=12.; design_draught_m=5.
    density_kg_m3=1025.; _geometry_digest='analytic-box-v1'
    x_m=np.array([20.,60.]); integration_weights_m=np.array([40.,40.])
    sections=[[np.array([[-6.,0.],[6.,0.],[6.,12.],[-6.,12.]])]]

    _axes=HullGeometry._axes
    def submerged(self,ta,tf,heel=0.,density=None):
        rho=1025. if density is None else density
        mean=(ta+tf)/2; trim=ta-tf; L=self.lpp_m; B=self.beam_m
        volume=B*L*mean
        x=L/2-L*trim/(12*mean)
        y=B**2*np.tan(heel)/(12*mean)
        z=(mean**2/2+trim**2/24+B**2*np.tan(heel)**2/24)/mean
        return Submerged(volume,rho*volume,x,y,z,B*L,L/2,L*B**3/12,B*L**3/12,
                         self.x_m,np.array([]),self.integration_weights_m)
    full_submerged=submerged


@pytest.fixture
def grid():
    return dict(immersion_m=np.linspace(3.,7.,9),trim_m=np.linspace(-2.,2.,5),
                heel_rad=np.radians(np.linspace(0.,12.,7)))


@pytest.fixture
def table(tmp_path,grid):
    return load_table_geometry(BoxGeometry(),{'hull':'box'},1025.,build=True,
                               grid=grid,cache_dir=tmp_path)


def test_tensor_cubic_reproduces_polynomial_exactly():
    axes=[np.linspace(-1,1,8),np.linspace(-2,2,7),np.linspace(0,1,6)]
    grid=dict(zip(('immersion_m','trim_m','heel_rad'),axes))
    q,t,h=np.meshgrid(*axes,indexing='ij')
    polynomial=4+q**3+2*t*t+3*h+q*t*h
    values=np.repeat(polynomial[...,None],4,axis=-1)
    adapter=TableGeometry(BoxGeometry(),grid,values,1025.)
    for point in [(-.95,-1.95,.01),(.17,.83,.61),(.99,1.99,.99)]:
        x,y,z=point
        expected=4+x**3+2*y*y+3*z+x*y*z
        assert adapter._values(*point)==pytest.approx(np.repeat(expected,4),abs=1e-12)


def test_no_extrapolation_or_density_substitution(table):
    with pytest.raises(OutsideTable): table.submerged(20.,20.)
    with pytest.raises(OutsideTable): table.submerged(5.,5.,density_kg_m3=1000.)
    with pytest.raises(OutsideTable): table.free_trim_curve([.8],4e6,40.,3.,1025.)


def test_closed_form_box_free_trim_and_gz(table):
    direct=table.direct; mass=1025.*80.*12.*5.; kg=3.
    phi=np.radians(np.arange(0.,12.1,1.))
    gz,drafts=table.free_trim_curve(phi,mass,40.,kg,1025.)
    bm=12.**2/(12*5.)
    exact=(2.5+bm-kg+.5*bm*np.tan(phi)**2)*np.sin(phi)
    assert gz==pytest.approx(exact,abs=2e-6)
    assert drafts==pytest.approx(np.full((len(phi),2),5.),abs=2e-6)
    cond=table.equilibrium(mass,40.,0.,kg,1025.)
    assert cond.mean_draught_m==pytest.approx(5.,abs=1e-8)
    assert cond.heel_rad==pytest.approx(0.,abs=1e-8)


def test_physical_kg_retained_in_longitudinal_balance(table):
    direct=table.direct; ta,tf=5.4,4.6; phi=.12; kg=4.
    state=direct.submerged(ta,tf,phi)
    slope=(tf-ta)/direct.lpp_m
    lcg=state.lcb_m+slope*(state.vcb_m-kg)
    gz,drafts=table.free_trim_curve([phi],state.displacement_kg,lcg,kg,1025.)
    assert drafts[0]==pytest.approx([ta,tf],abs=3e-6)
    _,along,_=direct._axes(*drafts[0],phi)
    s=direct.submerged(*drafts[0],phi)
    assert abs(np.dot(np.array(s.centroid_m)-[lcg,0.,kg],along))<1e-6


def test_tank_callback_is_called_and_controls_trim(table):
    calls=[]
    def callback(phi,trim):
        calls.append((phi,trim)); return [40.1,0.,4.]
    mass=5.*80.*12.*1025.
    _,drafts=table.free_trim_curve([.1,.12],mass,40.,4.,1025.,cg_callback=callback)
    assert len(calls)>2
    assert np.all(drafts[:,0] < drafts[:,1])


def test_cache_roundtrip_and_provenance(tmp_path,grid):
    direct=BoxGeometry(); params={'hull':'synthetic'}
    assert load_table_geometry(direct,params,1025.,cache_dir=tmp_path) is None
    first=load_table_geometry(direct,params,1025.,build=True,grid=grid,cache_dir=tmp_path)
    loaded=load_table_geometry(direct,params,1025.,cache_dir=tmp_path)
    assert loaded.values==pytest.approx(first.values)
    assert loaded.metadata['density_kg_m3']==1025.
    assert load_table_geometry(direct,params,1000.,cache_dir=tmp_path) is None
    assert load_table_geometry(direct,{'hull':'changed'},1025.,cache_dir=tmp_path).cache_path == first.cache_path
    assert not list(tmp_path.glob('*.partial.npz'))


def test_negative_heel_reflection(table):
    positive=table.submerged(5.,5.,.1)
    negative=table.submerged(5.,5.,-.1)
    assert positive.lcb_m==pytest.approx(negative.lcb_m)
    assert positive.vcb_m==pytest.approx(negative.vcb_m)
    assert positive.tcb_m==pytest.approx(-negative.tcb_m)


def test_full_waterplane_is_exact_and_memoized(table,monkeypatch):
    calls=[]; original=table.direct.full_submerged
    def full(*args): calls.append(args); return original(*args)
    monkeypatch.setattr(table.direct,'full_submerged',full)
    one=table.full_submerged(5.1,4.9,0.,1025.)
    two=table.full_submerged(5.1,4.9,0.,1025.)
    assert one is two
    assert len(calls)==1
    assert one.transverse_inertia_m4==80.*12.**3/12


def test_unqualified_table_never_enters_checker_cache(table,tmp_path):
    from stowage.checker.stability_tables import cached_geometry,qualify_table
    args=(table.direct,{'hull':'box'},1025.)
    assert cached_geometry(*args,cache_dir=tmp_path) is None
    stats=dict(max_l5_fraction=dict(gz=.1,area=.1,gm=.1),failure_count=0,sample_count=20)
    qualify_table(table,stats)
    assert cached_geometry(*args,cache_dir=tmp_path) is not None
    stats['max_l5_fraction']['gm']=.21
    with pytest.raises(ValueError): qualify_table(table,stats)
    # The certificate cannot qualify bytes other than the actual tested table.
    with table.cache_path.open('ab') as handle: handle.write(b'changed')
    assert cached_geometry(*args,cache_dir=tmp_path) is None


def test_geometry_reused_for_new_density(table,tmp_path,grid,monkeypatch):
    def forbidden(*args): raise AssertionError('density must not repeat geometry integration')
    monkeypatch.setattr(table.direct,'submerged',forbidden)
    fresh=load_table_geometry(table.direct,{'hull':'box'},1000.,build=True,grid=grid,cache_dir=tmp_path)
    assert fresh.values==pytest.approx(table.values)
    assert fresh.cache_path != table.cache_path
    assert fresh.submerged(5.,5.).displacement_kg==pytest.approx(1000.*80.*12.*5.)
    assert table.kn_grid.shape==table.values.shape[:3]+(2,)


def test_nonzero_tcg_equilibrium_matches_exact_box(table):
    ta,tf,phi=5.3,4.7,.1
    direct=table.direct; state=direct.submerged(ta,tf,phi)
    _,along,across=direct._axes(ta,tf,phi)
    kg=3.; slope=(tf-ta)/direct.lpp_m
    lcg=state.lcb_m+slope*(state.vcb_m-kg)
    tcg=(np.dot(np.asarray(state.centroid_m)-[lcg,0.,kg],across)/across[1])
    result=table.equilibrium(state.displacement_kg,lcg,tcg,kg,1025.)
    assert result.t_aft_m==pytest.approx(ta,abs=1e-5)
    assert result.t_fwd_m==pytest.approx(tf,abs=1e-5)
    assert result.heel_rad==pytest.approx(phi,abs=2e-6)


def test_asymmetric_hull_is_rejected_before_cache_build(tmp_path,grid):
    direct=BoxGeometry()
    direct.sections=[[np.array([[-6.,0.],[5.,0.],[5.,12.],[-6.,12.]])]]
    with pytest.raises(ValueError,match='bilateral'):
        load_table_geometry(direct,{'hull':'asymmetric'},1025.,build=True,grid=grid,cache_dir=tmp_path)


def test_mass_below_lightship_restarts_direct(table):
    table.qualified_min_mass_kg=4.5e6
    with pytest.raises(OutsideTable,match='lightship'):
        table.equilibrium(4e6,40.,0.,3.,1025.)
    with pytest.raises(OutsideTable,match='lightship'):
        table.free_trim_curve([0.,.1],4e6,40.,3.,1025.)


def test_runtime_source_edit_reuses_integrals_but_invalidates_certificate(tmp_path,grid,monkeypatch):
    import stowage.checker.stability_tables as module
    direct=BoxGeometry(); params={'hull':'box'}
    first=load_table_geometry(direct,params,1025.,build=True,grid=grid,cache_dir=tmp_path)
    module.qualify_table(first,dict(max_l5_fraction=dict(gz=.1,area=.1,gm=.1),
                                   failure_count=0,sample_count=20))
    original_metadata=module.cache_metadata
    def changed(*args):
        metadata=original_metadata(*args)
        metadata['builder_version'] += 1
        return metadata
    monkeypatch.setattr(module,'cache_metadata',changed)
    def forbidden(*args): raise AssertionError('raw geometry must be reused')
    monkeypatch.setattr(direct,'submerged',forbidden)
    second=load_table_geometry(direct,params,1025.,build=True,grid=grid,cache_dir=tmp_path)
    assert second.cache_path != first.cache_path
    assert second.values==pytest.approx(first.values)
    assert module.cached_geometry(direct,params,1025.,cache_dir=tmp_path) is None


def test_old_raw_cache_migrates_only_after_exact_geometry_match(tmp_path,grid,monkeypatch):
    import stowage.checker.stability_tables as module
    direct=BoxGeometry(); params={'hull':'box'}
    first=load_table_geometry(direct,params,1025.,build=True,grid=grid,cache_dir=tmp_path)
    for path in tmp_path.glob('geometry-*.npz'): path.unlink()
    original_metadata=module.cache_metadata
    def changed(*args):
        metadata=original_metadata(*args)
        metadata['builder_version'] += 1
        return metadata
    monkeypatch.setattr(module,'cache_metadata',changed)
    def forbidden(*args): raise AssertionError('final tables must preserve compatible raw arrays')
    monkeypatch.setattr(direct,'submerged',forbidden)
    migrated=load_table_geometry(direct,params,1025.,build=True,grid=grid,cache_dir=tmp_path)
    assert migrated.values==pytest.approx(first.values)
    assert migrated.cache_path != first.cache_path


def test_scantling_mass_domain_is_density_scaled_and_exact(tmp_path,grid):
    direct=BoxGeometry()
    params={'hull':{'lightship_mass_kg':2e6,'scantling_draught_m':6.}}
    sea=load_table_geometry(direct,params,1025.,build=True,grid=grid,cache_dir=tmp_path)
    fresh=load_table_geometry(direct,params,1000.,build=True,grid=grid,cache_dir=tmp_path)
    assert sea.qualified_max_mass_kg==pytest.approx(6.*80.*12.*1025.)
    assert fresh.qualified_max_mass_kg==pytest.approx(6.*80.*12.*1000.)
    for table in (sea,fresh):
        mass=table.qualified_max_mass_kg
        assert table.equilibrium(mass,40.,0.,3.,table.density_kg_m3).mean_draught_m==pytest.approx(6.)
        with pytest.raises(OutsideTable,match='scantling'):
            table.equilibrium(mass+1.,40.,0.,3.,table.density_kg_m3)
        with pytest.raises(OutsideTable,match='scantling'):
            table.free_trim_curve([0.,.1],mass+1.,40.,3.,table.density_kg_m3)


def test_unrelated_versions_and_tank_background_do_not_invalidate(tmp_path,grid,monkeypatch):
    import stowage.checker.stability_tables as module
    from stowage import versions
    direct=BoxGeometry();params={'hull':'box'}
    first=load_table_geometry(direct,params,1025.,build=True,grid=grid,cache_dir=tmp_path)
    module.qualify_table(first,dict(max_l5_fraction=dict(gz=.1,area=.1,gm=.1),failure_count=0,sample_count=4))
    monkeypatch.setattr(versions,'component_versions',lambda:{'checker':'changed','ballast':'changed'})
    changed=dict(params,tanks=[{'id':'new'}],fixed_loads=[{'mass_kg':100.}])
    second=module.cached_geometry(direct,changed,1025.,cache_dir=tmp_path)
    assert second is not None
    assert second.cache_path == first.cache_path


def test_final_metadata_has_only_builder_and_physical_dependencies(table):
    assert set(table.metadata)=={'format_version','builder_version','vessel','geometry_digest','density_kg_m3'}


@pytest.mark.parametrize('nonfinite_coordinates',[False,True])
def test_nonfinite_solver_residual_fails_closed(table,monkeypatch,nonfinite_coordinates):
    import stowage.checker.stability_tables as module
    calls=[]
    point=np.array([np.nan,np.nan]) if nonfinite_coordinates else np.array([5.,0.])
    def nonfinite_root(*args,**kwargs):
        calls.append('root'); return SimpleNamespace(x=point)
    def nonfinite_retry(*args,**kwargs):
        calls.append('retry'); return SimpleNamespace(x=point)
    monkeypatch.setattr(module,'root',nonfinite_root)
    monkeypatch.setattr(module,'least_squares',nonfinite_retry)
    with pytest.raises(OutsideTable,match='equilibrium'):
        table.free_trim_curve([.1],5.*80.*12.*1025.,40.,3.,1025.,
                              cg_callback=lambda *args:[np.nan,0.,3.])
    assert calls==['root','retry']


def test_incomplete_checkpoint_does_not_replace_complete_migrated_arrays(tmp_path,grid,monkeypatch):
    import stowage.checker.stability_tables as module
    direct=BoxGeometry(); params={'hull':'box'}
    first=load_table_geometry(direct,params,1025.,build=True,grid=grid,cache_dir=tmp_path)
    original_metadata=module.cache_metadata
    def changed(*args):
        metadata=original_metadata(*args)
        metadata['builder_version'] += 1
        return metadata
    monkeypatch.setattr(module,'cache_metadata',changed)
    key=module._digest(changed(direct,params,1025.))
    grid_key=module._digest({k:v.tolist() for k,v in grid.items()})[:16]
    partial=tmp_path/(key+'-'+grid_key+'.partial.npz')
    np.savez_compressed(partial,values=np.full(first.values.shape,np.nan))
    def forbidden(*args): raise AssertionError('complete raw cache must win over incomplete checkpoint')
    monkeypatch.setattr(direct,'submerged',forbidden)
    second=load_table_geometry(direct,params,1025.,build=True,grid=grid,cache_dir=tmp_path)
    assert second.values==pytest.approx(first.values)
    assert not partial.exists()


def test_wider_grid_integrates_only_new_exact_nodes(tmp_path,grid,monkeypatch):
    direct=BoxGeometry(); params={'hull':'box'}
    previous=load_table_geometry(direct,params,1025.,build=True,grid=grid,cache_dir=tmp_path)
    larger=dict(grid,trim_m=np.r_[-3.,grid['trim_m'],3.])
    calls=[]; original=direct.submerged
    def record(ta,tf,phi=0.,rho=None):
        calls.append((ta-tf,phi)); return original(ta,tf,phi,rho)
    monkeypatch.setattr(direct,'submerged',record)
    widened=load_table_geometry(direct,params,1025.,build=True,grid=larger,cache_dir=tmp_path)
    expected=2*len(grid['immersion_m'])*len(grid['heel_rad'])
    assert len(calls)==expected
    assert widened.values[:,1:-1]==pytest.approx(previous.values,abs=0.)
    assert all(abs(abs(trim)-3.)<1e-12 for trim,phi in calls)


def test_default_wider_grid_preserves_original_trim_nodes_exactly():
    from stowage.checker.stability_tables import default_grid
    geometry=BoxGeometry()
    axis=default_grid(geometry)['trim_m']
    assert len(axis)==19
    inner=np.linspace(-.02*geometry.lpp_m,.02*geometry.lpp_m,7)
    step=inner[1]-inner[0]
    previous=np.r_[inner[0]-step*np.arange(3,0,-1),inner,inner[-1]+step*np.arange(1,4)]
    assert np.array_equal(axis[3:-3],previous)
    assert np.array_equal(axis[6:13],inner)
    assert axis[[0,-1]]==pytest.approx([-.06*geometry.lpp_m,.06*geometry.lpp_m])


@pytest.mark.parametrize('version_name,next_version',[('STABILITY_TABLES_VERSION',2),('FORMAT_VERSION',3)])
def test_legacy_qualification_cannot_cross_future_builder_version(tmp_path,grid,monkeypatch,version_name,next_version):
    import stowage.checker.stability_tables as module
    direct=BoxGeometry();params={'hull':'box'}
    table=load_table_geometry(direct,params,1025.,build=True,grid=grid,cache_dir=tmp_path)
    module.qualify_table(table,dict(max_l5_fraction=dict(gz=.1,area=.1,gm=.1),failure_count=0,sample_count=4))
    monkeypatch.setattr(module,version_name,next_version)
    monkeypatch.setattr(module,'cached_geometry',lambda *a,**kw:None)
    assert module.migrate_legacy(direct,params,1025.,cache_dir=tmp_path)=='missing'


def test_default_angle_domain_covers_ten_degree_departure_offset():
    from stowage.checker.stability_tables import default_grid
    angles=default_grid(BoxGeometry())['heel_rad']
    assert angles[0] == 0.
    assert angles[-1] == pytest.approx(np.radians(70.))
    assert np.array_equal(angles[:31],np.radians(np.linspace(0.,60.,31)))
    # Signed heel uses exact reflection of the verified bilateral hull.
    assert np.radians(60.+10.) <= angles[-1]


def test_one_ulp_asymmetry_cannot_overwrite_false_bilateral_proof():
    from stowage.checker.stability_tables import cache_metadata
    direct=BoxGeometry()
    asymmetric=direct.sections[0][0].copy()
    asymmetric[1,0]=np.nextafter(asymmetric[1,0],np.inf)
    direct.sections=[[asymmetric]]
    direct._stability_table_bilateral=False
    with pytest.raises(ValueError,match='bilateral'):
        cache_metadata(direct,{},1025.)
    assert direct._stability_table_bilateral is False
