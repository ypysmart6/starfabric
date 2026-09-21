#!/usr/bin/env python3
"""Build/cache the pinned gNB with RLS -> RRC radio-failure notification."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[2]
COMMIT='384636f4fcf46b8c86109790ff3e2cd242b53556'
REPOSITORY='https://github.com/aligungr/UERANSIM.git'


def build():
    folder=ROOT/'ntn/single-pc';patch=folder/'ueransim-rlf.patch';dockerfile=folder/'ueransim-gnb.Dockerfile'
    digest=hashlib.sha256(COMMIT.encode()+patch.read_bytes()+dockerfile.read_bytes()).hexdigest()
    image='starfabric/ueransim-gnb:3.2.6-rlf-'+digest[:16]
    def cmd(*args,**kw):
        if not kw.get('capture_output'): kw.setdefault('stdout',sys.stderr)
        return subprocess.run(args,check=True,**kw)
    cached=subprocess.run(['docker','image','inspect',image],capture_output=True,text=True)
    if cached.returncode:
        source=ROOT/'.cache/ntn/UERANSIM'
        if not (source/'.git').exists(): cmd('git','clone','--depth','1','--branch','v3.2.6',REPOSITORY,str(source))
        origin=cmd('git','-C',str(source),'remote','get-url','origin',capture_output=True,text=True).stdout.strip()
        if origin!=REPOSITORY: raise RuntimeError('unexpected UERANSIM source origin')
        head=cmd('git','-C',str(source),'rev-parse','HEAD',capture_output=True,text=True).stdout.strip()
        if head!=COMMIT: raise RuntimeError('unexpected UERANSIM source revision')
        context=ROOT/'.cache/ntn'/('ueransim-build-'+digest[:16]);context.mkdir(parents=True,exist_ok=True)
        archive=cmd('git','-C',str(source),'archive',COMMIT,capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream: stream.extractall(context,filter='data')
        (context/'starfabric-rlf.patch').write_bytes(patch.read_bytes())
        (context/'UPSTREAM_COMMIT').write_text(COMMIT+'\n')
        cmd('docker','build','--progress=plain','-f',str(dockerfile),'-t',image,str(context))
    inspected=json.loads(cmd('docker','image','inspect',image,capture_output=True,text=True).stdout)[0]
    (ROOT/'reports').mkdir(exist_ok=True)
    (ROOT/'reports/ueransim-gnb-build.json').write_text(json.dumps({'image':image,'image_id':inspected['Id'],
        'source_commit':COMMIT,'source_repository':REPOSITORY,'patch_sha256':hashlib.sha256(patch.read_bytes()).hexdigest(),
        'dockerfile_sha256':hashlib.sha256(dockerfile.read_bytes()).hexdigest()},indent=2)+'\n')
    return image


if __name__=='__main__':
    print(build())
