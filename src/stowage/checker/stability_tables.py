"""Disk-backed geometric cross curves with exact free-trim moment equations.

Loading instruments interpolate precomputed hydrostatic/cross-curve data (IACS
UR L5). This table stores volume and first moments on immersion, trim and heel
axes, then solves the SAME buoyancy and weight equations as the direct polygon
method. Immersion is mean draught times cos(heel), finite at large heel. Using
physical KG in the longitudinal equation avoids silently substituting the
baseline-G KN trim for a loaded ship's free trim. Local tensor-product cubic
Lagrange interpolation uses four nodes per axis; no extrapolation is allowed.

APPROXIMATION: interpolation replaces polygon integration inside the published
cached grid. Error must be measured against the direct method before a table is
used for acceptance. Outside-grid conditions raise OutsideTable so the caller
can restart with direct integration. Full waterplane properties remain exact.
The cache fingerprint includes the hull geometry, qualified mass domain, grid
and this builder's own version (D30); density is exact, not
rounded. Only explicit build=True creates a table; checks never build tables.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
from scipy.optimize import root, least_squares

from stowage.vessel.hydrostatics import FloatingCondition, Submerged
from datetime import datetime, timezone

CACHE_DIR = Path(__file__).resolve().parents[3] / 'experiments/cache/stability'
FORMAT_VERSION = 2
STABILITY_TABLES_VERSION = 1
# Exact predecessor: only metadata/key/certificate code changed in D30.
LEGACY_STABILITY_TABLES_SHA256 = "d0891ebc5d6176f348d09ccf42d57a1243cad3dbb800691ae4c2ffdf5b76f92a"


class OutsideTable(ValueError):
    """The physical condition cannot be interpolated without extrapolation."""


def default_grid(geometry):
    """Operational draughts, trims +/-6% Lpp and heels through 70 degrees.

    The 70-degree coverage includes a ten-degree departure-list offset beyond
    the preceding 60-degree cross curves, on either side by hull reflection.
    Mass coverage remains lightship through upright scantling displacement.
    ASSUMPTION: this is a cache coverage choice, not an operating restriction;
    conditions beyond it are always evaluated by the direct reference method.
    """
    inner=np.linspace(-.02*geometry.lpp_m,.02*geometry.lpp_m,7)
    step=inner[1]-inner[0]
    # Retain the previous +/-4% grid nodes byte-for-byte so broadening coverage
    # never repeats their expensive direct polygon integration.
    trim=np.r_[inner[0]-step*np.arange(6,0,-1),inner,
               inner[-1]+step*np.arange(1,7)]
    return dict(immersion_m=np.linspace(-.1*geometry.design_draught_m,
                          geometry.depth_m+geometry.beam_m*.15, 33),
                trim_m=trim,
                heel_rad=np.radians(np.linspace(0.,70.,36)))


def _normalise_grid(grid):
    result = {k: np.asarray(grid[k], dtype=float) for k in
              ('immersion_m','trim_m','heel_rad')}
    for key, axis in result.items():
        if axis.ndim != 1 or len(axis)<4 or not np.isfinite(axis).all() or np.any(np.diff(axis)<=0):
            raise ValueError(f'{key}: at least four ascending finite nodes required')
    if result['heel_rad'][0] != 0 or result['heel_rad'][-1] >= np.pi/2-1e-5:
        raise ValueError('heel grid must start at zero and end below 90 degrees')
    return result


def cache_metadata(direct, params, density):
    """Only numerical hull inputs, coverage limits and this builder version."""
    if not np.isfinite(density) or density <= 0:
        raise ValueError('positive finite density required')
    if not getattr(direct,'_stability_table_bilateral',False):
        # The same exact contour-cycle proof guards both direct reflection
        # and tabulated reflection. Rounded unordered edges could otherwise
        # overwrite an explicitly false proof for a slightly asymmetric hull.
        from stowage.checker.stability_geometry import _bilateral_sections
        if not _bilateral_sections(direct.sections):
            raise ValueError('heel reflection table requires bilateral hull symmetry')
        direct._stability_table_bilateral=True
    hull=params.get('hull',{}) if isinstance(params,dict) else {}
    limits={k:hull[k] for k in ('lightship_mass_kg','scantling_draught_m','max_draught_m')
            if isinstance(hull,dict) and k in hull}
    return dict(format_version=FORMAT_VERSION, builder_version=STABILITY_TABLES_VERSION,
                vessel={'hull':limits}, geometry_digest=str(direct._geometry_digest),
                density_kg_m3=float(density))


def _digest(metadata):
    return hashlib.sha256(json.dumps(metadata,sort_keys=True,default=str,
                                    separators=(',',':')).encode()).hexdigest()


def _raw_geometry_metadata(metadata):
    """Raw integrals use their own builder version, FORMAT_VERSION.

    Bump FORMAT_VERSION when direct polygon integration changes, and bump
    STABILITY_TABLES_VERSION when interpolation or equilibrium changes. Neither
    version is the shared checker version. Density is absent from raw geometric
    integrals; it remains a dependency of the final table and certificate.
    """
    return dict(format_version=metadata['format_version'],
                geometry_digest=metadata['geometry_digest'])


def _compatible_raw_values(directory,metadata,grid,shape):
    """Recover matching raw nodes, including overlap with a smaller old grid."""
    expected=_digest(_raw_geometry_metadata(metadata))
    recovered=np.full(shape,np.nan)
    found=False
    for candidate in sorted(directory.glob('*.npz')):
        if candidate.name.startswith('geometry-') or '.partial.' in candidate.name or '.tmp.' in candidate.name:
            continue
        try:
            with np.load(candidate,allow_pickle=False) as old:
                previous=json.loads(str(old['metadata']))
                if 'source_sha256' in previous:
                    names=('stability_geometry.py','hydrostatics.py','hull_offsets.py')
                    files={name:(Path(__file__).with_name(name) if name.startswith('stability_') else
                                   Path(__file__).parents[1]/'vessel'/name) for name in names}
                    if any(previous['source_sha256'].get(name) != hashlib.sha256(file.read_bytes()).hexdigest()
                           for name,file in files.items()): continue
                if _digest(_raw_geometry_metadata(previous)) != expected: continue
                values=old['values']
                previous_indices=[]; new_indices=[]
                for name,axis in grid.items():
                    previous=[]; current=[]
                    for index,value in enumerate(old[name]):
                        matches=np.flatnonzero(np.isclose(axis,value,rtol=0.,atol=1e-10))
                        if len(matches):
                            previous.append(index); current.append(int(matches[0]))
                    previous_indices.append(previous); new_indices.append(current)
                if any(not indices for indices in previous_indices): continue
                block=values[np.ix_(*previous_indices)]
                if not np.isfinite(block).all(): continue
                recovered[np.ix_(*new_indices)]=block
                found=True
                if np.isfinite(recovered).all(): return recovered
        except (OSError,ValueError,KeyError):
            continue
    return recovered if found else None


def _basis_reference(axis, value):
    if value < axis[0]-1e-10 or value > axis[-1]+1e-10:
        raise OutsideTable('condition exceeds cached geometric table')
    value = np.clip(value,axis[0],axis[-1])
    start = int(np.clip(np.searchsorted(axis,value)-2,0,len(axis)-4))
    nodes=axis[start:start+4]
    weights=np.ones(4)
    for j in range(4):
        for k in range(4):
            if j != k:
                weights[j] *= (value-nodes[k])/(nodes[j]-nodes[k])
    return start,weights


def _basis(axis, value):
    if _basis_dependencies() != _ORIGINAL_BASIS_DEPENDENCIES:
        return _basis_reference(axis, value)
    from stowage.checker.stability_reuse import axis_query
    return axis_query(_basis_reference, axis, value)


def _basis_dependencies():
    functions = (_basis_reference, np.clip, np.searchsorted, np.ones, OutsideTable, int, len, range)
    return (*functions, *(getattr(function, '__code__', None) for function in functions))


_ORIGINAL_BASIS_DEPENDENCIES = _basis_dependencies()


class TableGeometry:
    """Geometry adapter; all noninterpolated hull operations delegate directly."""
    def __init__(self,direct,grid,values,density,metadata=None,cache_path=None,kn_grid=None):
        self.direct=direct
        self.grid=_normalise_grid(grid)
        self.values=np.asarray(values,dtype=float)
        expected=tuple(len(a) for a in self.grid.values())+(4,)
        if self.values.shape != expected or not np.isfinite(self.values).all():
            raise ValueError('invalid cached geometric array')
        self.density_kg_m3=float(density)
        self.metadata=metadata or {}
        vessel=self.metadata.get('vessel',{})
        hull=vessel.get('hull',{}) if isinstance(vessel,dict) else {}
        self.qualified_min_mass_kg=float(hull.get('lightship_mass_kg',0.)) if isinstance(hull,dict) else 0.
        self.qualified_max_mass_kg=np.inf
        if isinstance(hull,dict):
            maximum=hull.get('scantling_draught_m',hull.get('max_draught_m'))
            if maximum is not None:
                maximum=float(maximum)
                volumes=getattr(direct,'_stability_table_scantling_volumes',{})
                if maximum not in volumes:
                    volumes[maximum]=direct.submerged(maximum,maximum,0.,density).volume_m3
                    direct._stability_table_scantling_volumes=volumes
                self.qualified_max_mass_kg=volumes[maximum]*density
        self.cache_path=None if cache_path is None else Path(cache_path)
        self.kn_grid=kn_grid
        self._full_cache={}
        self._last_curve=None

    def __getattr__(self,name):
        return getattr(self.direct,name)

    @property
    def bounds(self):
        return {k:(float(v[0]),float(v[-1])) for k,v in self.grid.items()}

    def _values(self,q,trim,phi):
        indices,weights=zip(*[_basis(a,v) for a,v in zip(self.grid.values(),(q,trim,abs(phi)))])
        i,j,k=indices
        result=np.einsum('i,j,k,ijkn->n',*weights,self.values[i:i+4,j:j+4,k:k+4],optimize=False)
        if phi < 0: result[2] *= -1
        return result

    def submerged(self,ta,tf,heel=0.,density_kg_m3=None):
        rho=self.density_kg_m3 if density_kg_m3 is None else density_kg_m3
        if rho != self.density_kg_m3:
            raise OutsideTable('different water density requires its own table')
        volume,mx,my,mz=self._values((ta+tf)/2*np.cos(heel),ta-tf,heel)
        if volume <= 1e-9:
            raise OutsideTable('dry or nonpositive interpolated volume')
        return Submerged(float(volume),float(volume*rho),float(mx/volume),float(my/volume),float(mz/volume),
                         0.,self.lpp_m/2,0.,0.,self.x_m,np.array([]),self.integration_weights_m)

    def full_submerged(self,ta,tf,heel=0.,density=None):
        density=self.density_kg_m3 if density is None else density
        key=(float(ta),float(tf),float(heel),density)
        if key not in self._full_cache:
            self._full_cache[key]=self.direct.full_submerged(ta,tf,heel,density)
        return self._full_cache[key]

    def _condition(self,mass,cg,rho,phi,seed=None,cg_callback=None):
        """Solve volume and exact physical longitudinal/transverse moment balance."""
        if mass < self.qualified_min_mass_kg:
            raise OutsideTable('mass below vessel lightship is outside qualified table domain')
        if mass > self.qualified_max_mass_kg:
            # The qualified operational domain ends at upright even-keel
            # scantling displacement, with no buffer. This is table coverage,
            # not a substitute for actual aft/forward draught-limit checks.
            raise OutsideTable('mass above upright scantling displacement is outside qualified table domain')
        if rho != self.density_kg_m3:
            raise OutsideTable('different water density requires its own table')
        fixed=phi is not None
        qlo,qhi=self.bounds['immersion_m']; tlo,thi=self.bounds['trim_m']
        phimax=self.grid['heel_rad'][-1]
        if seed is None:
            q=np.clip(mass/rho/(self.lpp_m*self.beam_m*.7),qlo+1e-7,qhi-1e-7)
            seed=np.array([q,0.] if fixed else [q,0.,0.])
        else: seed=np.asarray(seed,dtype=float)
        def residual(v):
            q,trim=v[:2]; angle=phi if fixed else v[2]
            try:
                volume,mx,my,mz=self._values(q,trim,angle)
            except OutsideTable:
                # A bounded least-squares retry follows an unconstrained root.
                raise
            centroid=np.array([mx,my,mz])/max(volume,1e-20)
            mean=q/np.cos(angle); ta,tf=mean+trim/2,mean-trim/2
            _,along,transverse=self._axes(ta,tf,angle)
            physical=np.asarray(cg_callback(angle,np.arctan(trim/self.lpp_m)) if cg_callback else cg)
            diff=centroid-physical
            ans=[(volume-mass/rho)/(mass/rho),np.dot(diff,along)/self.lpp_m]
            if not fixed: ans.append(np.dot(diff,transverse)/self.beam_m)
            return ans
        try:
            solved=root(residual,seed,tol=1e-9)
            error=np.linalg.norm(residual(solved.x)) if np.isfinite(solved.x).all() else np.inf
        except OutsideTable:
            error=np.inf
        if not np.isfinite(error) or error > 2e-6:
            lower=[qlo,tlo] if fixed else [qlo,tlo,-phimax]
            upper=[qhi,thi] if fixed else [qhi,thi,phimax]
            seed=np.clip(seed,np.array(lower)+1e-9,np.array(upper)-1e-9)
            solved=least_squares(residual,seed,bounds=(lower,upper),diff_step=1e-4,
                                 xtol=2e-10,ftol=2e-10,gtol=2e-10,max_nfev=100)
            error=np.linalg.norm(residual(solved.x)) if np.isfinite(solved.x).all() else np.inf
        if not np.isfinite(error) or error > 2e-6:
            raise OutsideTable('no equilibrium inside cached table')
        q,trim=solved.x[:2]; angle=float(phi if fixed else solved.x[2])
        mean=q/np.cos(angle); ta,tf=float(mean+trim/2),float(mean-trim/2)
        submerged=self.submerged(ta,tf,angle,rho)
        return FloatingCondition(ta,tf,angle,submerged,float(error)),np.asarray(solved.x)

    def equilibrium(self,mass_kg,lcg_m,tcg_m,vcg_m,density_kg_m3=None):
        rho=self.density_kg_m3 if density_kg_m3 is None else density_kg_m3
        return self._condition(mass_kg,[lcg_m,tcg_m,vcg_m],rho,None)[0]

    def free_trim_curve(self,angles,mass,lcg,kg,density,cg_callback=None,
                        initial_drafts=None,stop_when=None):
        angles=np.asarray(angles)
        seed=None
        if initial_drafts is not None and len(angles):
            ta,tf=initial_drafts
            seed=np.array([(ta+tf)/2*np.cos(angles[0]),ta-tf])
        gz,conditions=[],[]
        for phi in angles:
            if abs(phi)>self.grid['heel_rad'][-1]+1e-10:
                raise OutsideTable('heel exceeds cached cross curves')
            condition,seed=self._condition(mass,[lcg,0.,kg],density,float(phi),seed,cg_callback)
            _,_,across=self._axes(condition.t_aft_m,condition.t_fwd_m,phi)
            gz.append(float(np.dot(np.asarray(condition.submerged.centroid_m)-[lcg,0.,kg],across)))
            conditions.append((condition.t_aft_m,condition.t_fwd_m))
            if stop_when is not None and stop_when(phi,gz[-1],conditions[-1]): break
        return np.asarray(gz),np.asarray(conditions).reshape(-1,2)


def load_table_geometry(direct,params,density,*,build=False,grid=None,cache_dir=CACHE_DIR):
    """Load the newest matching grid, or explicitly build a requested grid.

    Builders are resumable: each completed immersion plane is atomically saved.
    A partial file is never returned to a checker. One worker, no GPU.
    """
    metadata=cache_metadata(direct,params,density)
    key=_digest(metadata)
    directory=Path(cache_dir)
    if grid is None and not build:
        matches=sorted(directory.glob(key+'-*.npz'),key=lambda p:p.stat().st_mtime,reverse=True)
        if not matches: return None
        path=matches[0]
    else:
        grid=_normalise_grid(default_grid(direct) if grid is None else grid)
        grid_key=_digest({k:v.tolist() for k,v in grid.items()})[:16]
        path=directory/(key+'-'+grid_key+'.npz')
    if path.exists():
        try:
            with np.load(path,allow_pickle=False) as data:
                stored=json.loads(str(data['metadata']))
                if stored != json.loads(json.dumps(metadata,default=str)): return None
                grid={k:data[k] for k in ('immersion_m','trim_m','heel_rad')}
                return TableGeometry(direct,grid,data['values'],density,stored,path,data['kn_grid'])
        except (ValueError,OSError,KeyError):
            if not build: return None
    if not build: return None
    directory.mkdir(parents=True,exist_ok=True)
    partial=path.with_suffix('.partial.npz')
    shape=tuple(len(x) for x in grid.values())+(4,)
    values=np.full(shape,np.nan)
    # Volume/first moments depend only on geometry and attitude, not density.
    # A new density gets its own provenance key and empirical certificate but
    # shares the identical geometric integration, never another expensive build.
    geometric_metadata=_raw_geometry_metadata(metadata)
    geometric_key=_digest(dict(metadata=geometric_metadata,
                               grid={k:v.tolist() for k,v in grid.items()}))
    geometric_path=directory/('geometry-'+geometric_key+'.npz')
    if geometric_path.exists():
        with np.load(geometric_path,allow_pickle=False) as old:
            if old['values'].shape == shape: values=old['values']
    else:
        compatible=_compatible_raw_values(directory,metadata,grid,shape)
        if compatible is not None: values=compatible
    if partial.exists() and not np.isfinite(values).all():
        with np.load(partial,allow_pickle=False) as old:
            checkpoint=old['values']
            if checkpoint.shape == shape:
                missing=~np.isfinite(values).all(axis=-1)
                valid=np.isfinite(checkpoint).all(axis=-1)
                values[missing & valid]=checkpoint[missing & valid]
    for i,q in enumerate(grid['immersion_m']):
        if np.isfinite(values[i]).all(): continue
        for j,trim in enumerate(grid['trim_m']):
            for k,phi in enumerate(grid['heel_rad']):
                if np.isfinite(values[i,j,k]).all(): continue
                mean=q/np.cos(phi)
                s=direct.submerged(mean+trim/2,mean-trim/2,phi,density)
                values[i,j,k]=[s.volume_m3,*(s.volume_m3*np.asarray(s.centroid_m))]
        temporary=partial.with_suffix('.tmp.npz')
        np.savez_compressed(temporary,values=values)
        os.replace(temporary,partial)
    temporary=path.with_suffix('.tmp.npz')
    # These are free-trim baseline-G cross curves: at each prescribed trim the
    # tabulated baseline LCG is the one whose longitudinal moment vanishes.
    # Interpolating volume/first moments instead of KN alone additionally permits
    # the exact physical-KG trim correction at runtime, with no extra KG axis.
    kn_grid=np.zeros(shape[:-1]+(2,))
    for i,q in enumerate(grid['immersion_m']):
        for j,trim in enumerate(grid['trim_m']):
            for k,phi in enumerate(grid['heel_rad']):
                volume=values[i,j,k,0]
                if volume<=1e-12: continue
                centre=values[i,j,k,1:]/volume
                lcg=centre[0]-trim/direct.lpp_m*centre[2]
                mean=q/np.cos(phi)
                _,_,across=direct._axes(mean+trim/2,mean-trim/2,phi)
                kn_grid[i,j,k]=[lcg,np.dot(centre-[lcg,0.,0.],across)]
    np.savez_compressed(temporary,metadata=json.dumps(metadata,sort_keys=True,default=str),
                        values=values,kn_grid=kn_grid,**grid)
    os.replace(temporary,path)
    if not geometric_path.exists():
        geometric_temporary=geometric_path.with_suffix('.tmp.npz')
        np.savez_compressed(geometric_temporary,values=values)
        os.replace(geometric_temporary,geometric_path)
    partial.unlink(missing_ok=True)
    return TableGeometry(direct,grid,values,density,metadata,path,kn_grid)


def cached_geometry(direct,params,density,*,cache_dir=CACHE_DIR):
    """Only independently measured, qualified tables enter checker dispatch."""
    table=load_table_geometry(direct,params,density,cache_dir=cache_dir)
    if table is None: return None
    try:
        certificate=json.loads(table.cache_path.with_suffix('.certificate.json').read_text())
        if certificate.get('inputs_hash') != _certificate_inputs(table) or certificate.get('builder_version') != STABILITY_TABLES_VERSION: return None
        if certificate['cache_sha256'] != hashlib.sha256(table.cache_path.read_bytes()).hexdigest(): return None
        _check_validation(certificate['validation'])
        return table
    except (OSError,ValueError,KeyError,TypeError):
        return None


def _check_validation(stats):
    fractions=stats['max_l5_fraction']
    if stats['failure_count'] != 0 or stats['sample_count'] < 4:
        raise ValueError('qualification requires successful independent reference samples')
    for name in ('gz','area','gm'):
        value=fractions[name]
        if not np.isfinite(value) or not 0 <= value <= .2:
            raise ValueError('table interpolation exceeds one fifth of L5 tolerance')


def _certificate_inputs(table):
    return _digest(dict(metadata=table.metadata,grid={k:v.tolist() for k,v in table.grid.items()}))


def qualify_table(table,validation_stats):
    """Persist empirical validation evidence, not a uniform mathematical bound.

    The validator supplies maximum error / IACS L5 tolerance for GZ, area, GM,
    plus sample_count and failure_count. Binding the certificate to every byte
    of the NPZ binds its grid, geometry, density and source/version metadata.
    """
    _check_validation(validation_stats)
    if table.cache_path is None: raise ValueError('qualification requires a disk-backed table')
    certificate=dict(inputs_hash=_certificate_inputs(table), builder_version=STABILITY_TABLES_VERSION,
                     date=datetime.now(timezone.utc).isoformat(), cache_sha256=hashlib.sha256(table.cache_path.read_bytes()).hexdigest(),
                     evidence_kind='empirical random direct-reference comparisons',
                     validation=validation_stats)
    path=table.cache_path.with_suffix('.certificate.json')
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(certificate,sort_keys=True,indent=2)+'\n')
    os.replace(temporary,path)
    return path


def migrate_legacy(direct,params,density,*,cache_dir=CACHE_DIR):
    """Explicit offline metadata-only migration of certified format-2 arrays.

    Requires the same hull digest, density, operational mass domain and direct
    integrator sources, plus the intact original SHA-bound error certificate.
    D30 changes only keys/certificate metadata, never interpolation equations.
    """
    if cached_geometry(direct,params,density,cache_dir=cache_dir) is not None: return 'reused'
    # This migration proves only the D30 metadata-only transition. A later
    # numerical builder must receive fresh direct-reference qualification.
    if STABILITY_TABLES_VERSION != 1 or FORMAT_VERSION != 2: return 'missing'
    expected=cache_metadata(direct,params,density)
    source_names=('stability_geometry.py','hydrostatics.py','hull_offsets.py')
    paths={name:(Path(__file__).with_name(name) if name.startswith('stability_') else
                       Path(__file__).parents[1]/'vessel'/name) for name in source_names}
    sources={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in paths.items()}
    directory=Path(cache_dir)
    for oldpath in sorted(directory.glob('*.npz'),key=lambda p:p.stat().st_mtime_ns,reverse=True):
        if oldpath.name.startswith(('geometry-','liquid-','sounding-')): continue
        try:
            evidence=json.loads(oldpath.with_suffix('.certificate.json').read_text())
            if evidence['cache_sha256'] != hashlib.sha256(oldpath.read_bytes()).hexdigest(): continue
            _check_validation(evidence['validation'])
            with np.load(oldpath,allow_pickle=False) as saved:
                old=json.loads(str(saved['metadata']))
                if old.get('source_sha256',{}).get('stability_tables.py') != LEGACY_STABILITY_TABLES_SHA256: continue
                if old.get('geometry_digest') != expected['geometry_digest'] or old.get('density_kg_m3') != density: continue
                if any(old.get('source_sha256',{}).get(name) != digest for name,digest in sources.items()): continue
                previous_hull=old.get('vessel',{}).get('hull',{})
                if any(previous_hull.get(k) != v for k,v in expected['vessel']['hull'].items()): continue
                grid={name:saved[name] for name in ('immersion_m','trim_m','heel_rad')}
                values=saved['values'];kn_grid=saved['kn_grid']
            _normalise_grid(grid)
            grid_key=_digest({k:v.tolist() for k,v in grid.items()})[:16]
            path=directory/(_digest(expected)+'-'+grid_key+'.npz')
            temporary=path.with_suffix('.tmp.npz')
            np.savez_compressed(temporary,metadata=json.dumps(expected,sort_keys=True),values=values,kn_grid=kn_grid,**grid)
            os.replace(temporary,path)
            table=TableGeometry(direct,grid,values,density,expected,path,kn_grid)
            stats=dict(evidence['validation'],legacy_evidence={'path':str(oldpath),
                       'sha256':evidence['cache_sha256'],'reason':'D30 metadata-only migration; identical numerical arrays and direct provider sources'})
            qualify_table(table,stats)
            return 'migrated'
        except (OSError,ValueError,TypeError,KeyError): continue
    return 'missing'
