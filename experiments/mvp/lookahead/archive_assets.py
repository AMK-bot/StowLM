"""Experimental HARNESS helper: declare, verify and copy only pinned offsets.

No production source is changed. Create a NEW archive; never repair an existing
published epoch in place. A benchmark must separately qualify its active screen.
"""
from pathlib import Path
import hashlib
import shutil
import tomllib


def offset_dependencies(root):
    root=Path(root)
    result={}
    for params in sorted((root/'params/fleet').glob('*.toml')):
        form=tomllib.loads(params.read_text()).get('hull_form',{})
        if form.get('family')!='stowlm_offsets_v1':
            continue
        relative=Path(form['offsets_path'])
        digest=form['offsets_sha256']
        if relative.is_absolute() or '..' in relative.parts or len(digest)!=64:
            raise ValueError(f'Invalid declared offsets dependency in {params}: {relative}, SHA256={digest}')
        if not (root/relative).resolve().is_relative_to(root.resolve()):
            raise ValueError(f'Offset dependency escapes archive root via symlink: {params}: {relative}')
        name=relative.as_posix()
        if name in result and result[name]['sha256']!=digest:
            raise ValueError(f'Conflicting offsets checksums: {name}')
        entry=result.setdefault(name,dict(sha256=digest,references=[]))
        entry['references'].append(str(params.relative_to(root)))
    if not result:
        raise ValueError(f'No declared offset dependencies found under {root}/params/fleet')
    return result


def verify_offset_dependencies(root):
    root=Path(root)
    dependencies=offset_dependencies(root)
    for name,entry in dependencies.items():
        path=root/name
        label=f'{name}; references={entry["references"]}; expected SHA256={entry["sha256"]}'
        if not path.is_file():
            raise FileNotFoundError(f'Archive missing declared offsets dependency: {label}; resolved={path}')
        actual=hashlib.sha256(path.read_bytes()).hexdigest()
        if actual!=entry['sha256']:
            raise ValueError(f'Archive offset checksum mismatch: {label}; actual SHA256={actual}')
    return dependencies


def copy_offset_dependencies(source,new_archive):
    source,new_archive=Path(source),Path(new_archive)
    dependencies=verify_offset_dependencies(source)
    for name in dependencies:
        destination=new_archive/name
        if destination.exists():
            raise FileExistsError(f'Refusing to overwrite an archived asset: {destination}')
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source/name,destination)
    assert verify_offset_dependencies(new_archive)==dependencies
    return dependencies


def require_active_securing(screen):
    if getattr(screen,'bounds_available',False) is not True:
        raise RuntimeError('Benchmark securing qualification failed: bounds_available=False; '
                           f'unavailable_reason={getattr(screen,"unavailable_reason",None)}')


def require_archive_root(archive,resolved_root):
    archive=Path(archive).resolve()
    marker=archive/'pyproject.toml'
    if not marker.is_file():
        raise FileNotFoundError(f'Archive project marker required before benchmark preflight: {marker}')
    actual=Path(resolved_root).resolve()
    if actual!=archive:
        raise RuntimeError(f'Physical resource root escapes archive: expected={archive}; actual={actual}')
    return archive
