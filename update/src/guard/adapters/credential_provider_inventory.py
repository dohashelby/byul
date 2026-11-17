"""Allowlisted provider declarations and worker name associations, never values.

Pod declarations do not prove provider resolution, credential use, or authority.
This inventory deliberately does not traverse literal values, volumeAttributes,
Secret/ConfigMap contents, command arguments, SDK configuration, or metadata URLs.
"""
import hashlib,json,re

SCHEMA='guard-credential-provider-declarations/1'
MAX_ROWS=1024


def _name(value):
    return type(value)is str and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,252}',value)is not None


def _ref(value):
    if type(value)is not str or not 0<len(value)<=1024:raise ValueError
    return hashlib.sha256(value.encode()).hexdigest()


def _list(value):
    if type(value)is not list or len(value)>MAX_ROWS:raise ValueError
    return value


def _path(value):
    if(type(value)is not str or not value.startswith('/')or len(value)>4096
            or any(v in{'.','..'}for v in value.split('/'))or '\x00'in value):raise ValueError
    return value


def _reference(value):
    if type(value)is not dict:raise ValueError
    result={'object_name_sha256':_ref(value.get('name'))}
    if 'key'in value:result['key_sha256']=_ref(value['key'])
    if 'optional'in value:
        if type(value['optional'])is not bool:raise ValueError
        result['optional']=value['optional']
    return result


def project(pod,binding):
    """The caller must bind both snapshots to the exact owned Pod generation."""
    try:
        if type(pod)is not dict or pod.get('kind')!='Pod':raise ValueError
        metadata=pod['metadata'];spec=pod['spec']
        if(metadata['uid']!=binding['pod_uid']or metadata['name']!=binding['pod']
                or metadata['namespace']!=binding['namespace']or metadata.get('deletionTimestamp')):raise ValueError
        containers=[c for c in _list(spec['containers'])if c.get('name')==binding['container']]
        if len(containers)!=1:raise ValueError
        container,=containers
        output={'schema':SCHEMA,'pod_uid':binding['pod_uid'],'container':binding['container'],
            'environment':[],'environment_sources':[],'mounted_sources':[],
            'declarations_complete':True,'diagnostic_codes':[],
            'provider_resolution_verified':False,'credential_use_verified':False,
            'provider_authority_verified':False,'whole_inheritance_verified':False}
        def gap(code):
            output['declarations_complete']=False
            if code not in output['diagnostic_codes']:output['diagnostic_codes'].append(code)
        seen=set()
        for item in _list(container.get('env',[])):
            try:
                name=item['name']
                if not _name(name)or name in seen:raise ValueError
                seen.add(name);row={'name':name}
                # Never inspect even the type/hash of the literal value.
                if 'valueFrom'not in item:row['source']='literal-declaration'
                else:
                    source=item['valueFrom']
                    if type(source)is not dict or len(source)!=1:raise ValueError
                    kind,=source
                    if kind in{'secretKeyRef','configMapKeyRef'}:row.update(source=kind,reference=_reference(source[kind]))
                    elif kind in{'fieldRef','resourceFieldRef'}:row['source']=kind
                    else:raise ValueError
                output['environment'].append(row)
            except(KeyError,TypeError,ValueError):gap('PROVIDER_ENV_DECLARATION_UNVERIFIED')
        for item in _list(container.get('envFrom',[])):
            try:
                prefix=item.get('prefix','')
                if prefix and not _name(prefix):raise ValueError
                kinds=[k for k in('secretRef','configMapRef')if k in item]
                if len(kinds)!=1:raise ValueError
                kind,=kinds
                output['environment_sources'].append({'kind':kind,'prefix':prefix,'reference':_reference(item[kind])})
            except(KeyError,TypeError,ValueError):gap('PROVIDER_ENVFROM_DECLARATION_UNVERIFIED')
        volumes=_list(spec.get('volumes',[]));volume_names=[v.get('name')for v in volumes]
        if len(set(volume_names))!=len(volume_names):raise ValueError
        for mount in _list(container.get('volumeMounts',[])):
            try:
                name=mount['name'];matches=[v for v in volumes if v['name']==name]
                if len(matches)!=1:raise ValueError
                volume,=matches;kinds=[k for k in volume if k!='name']
                if len(kinds)!=1:raise ValueError
                kind,=kinds;row={'volume_name_sha256':_ref(name),'mount_path':_path(mount['mountPath']),
                    'kind':kind,'read_only_declared':mount.get('readOnly',False)}
                if type(row['read_only_declared'])is not bool:raise ValueError
                if 'subPathExpr'in mount:row['dynamic_subpath_declared']=True
                if kind=='secret':row['reference']={'object_name_sha256':_ref(volume[kind]['secretName'])}
                elif kind=='configMap':row['reference']=_reference(volume[kind])
                elif kind=='projected':
                    sources=[]
                    for source in _list(volume[kind]['sources']):
                        if type(source)is not dict or len(source)!=1:raise ValueError
                        subkind,=source
                        if subkind in{'secret','configMap'}:sources.append({'kind':subkind,'reference':_reference(source[subkind])})
                        elif subkind in{'serviceAccountToken','downwardAPI'}:sources.append({'kind':subkind})
                        else:raise ValueError
                    row['sources']=sources
                elif kind=='csi':row['driver_sha256']=_ref(volume[kind]['driver'])
                elif kind not in{'hostPath','emptyDir','persistentVolumeClaim','downwardAPI'}:gap('PROVIDER_VOLUME_KIND_UNVERIFIED')
                output['mounted_sources'].append(row)
            except(KeyError,TypeError,ValueError):gap('PROVIDER_MOUNT_DECLARATION_UNVERIFIED')
        token=spec.get('automountServiceAccountToken')
        if token is not None and type(token)is not bool:raise ValueError
        output['automount_service_account_token_declared']=token
        return output
    except(KeyError,TypeError,ValueError):raise ValueError('CREDENTIAL_PROVIDER_DECLARATIONS_UNVERIFIED')from None


def associate(declarations,records,*,roster_complete):
    """Positive name presence only: neither value equivalence nor parent origin."""
    declared={row['name']for row in declarations['environment']}
    rows=[];seen=set()
    for record in _list(records):
        rank=record.get('rank');names=record.get('environment_names')
        if(type(rank)is not int or rank<0 or rank in seen or type(names)is not list
                or len(names)>MAX_ROWS or any(not _name(n)for n in names)):raise ValueError('CREDENTIAL_PROVIDER_WORKER_NAMES_UNVERIFIED')
        seen.add(rank);observed=set(names)
        rows.append({'rank':rank,'declared_names_observed':sorted(declared&observed),
            'declared_names_not_observed':sorted(declared-observed),
            'other_observed_name_count':len(observed-declared)})
    return {'workers':sorted(rows,key=lambda r:r['rank']),
        'selected_tp_roster_verified':roster_complete is True and bool(rows),
        'absence_proves_provider_nonuse':False,'value_equivalence_verified':False,
        'parent_inheritance_verified':False,'provider_use_verified':False}
