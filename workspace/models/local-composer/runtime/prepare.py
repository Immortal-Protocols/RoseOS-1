#!/usr/bin/env python3
"""Build Rose server's pinned local model worker. No listeners, app changes or server restart."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
RUNTIME = Path(__file__).resolve().parent
LLAMA_REV = "dca216ad63c586f0db465324f785c5a418faef58"
MODEL_REV = "90862c4b9d2787eaed51d12237eafdfe7c5f6077"
MODEL_SHA = "061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a"
MODEL_FILE = "Qwen3-1.7B-Q8_0.gguf"
MODEL_BYTES = 1834426016

def run(args, **kw):
    return subprocess.run([str(a) for a in args], check=True, **kw)

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--llama-source', type=Path)
    parser.add_argument('--model', type=Path, help='Existing pinned GGUF; otherwise hf uses its existing login')
    parser.add_argument('--output', type=Path, default=ROOT / '.tools/local-composer')
    parser.add_argument('--java-home', type=Path, default=os.environ.get('JAVA_HOME'))
    args = parser.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    source = args.llama_source or out / 'llama.cpp'
    if not source.exists():
        run(['git', 'init', source])
        run(['git', '-C', source, 'fetch', '--depth', '1', 'https://github.com/ggml-org/llama.cpp.git', LLAMA_REV])
        run(['git', '-C', source, 'checkout', '--detach', 'FETCH_HEAD'])
    actual = run(['git', '-C', source, 'rev-parse', 'HEAD'], stdout=subprocess.PIPE, text=True).stdout.strip()
    if actual != LLAMA_REV or run(['git', '-C', source, 'status', '--porcelain'], stdout=subprocess.PIPE, text=True).stdout:
        raise SystemExit('llama.cpp must be clean at the pinned revision')
    model = args.model
    if model is None:
        result = subprocess.run(['hf', 'download', 'Qwen/Qwen3-1.7B-GGUF', MODEL_FILE, '--revision', MODEL_REV,
                                 '--cache-dir', str(out / 'hf-cache')], capture_output=True, text=True)
        if result.returncode: raise SystemExit('Hugging Face download failed; no credential-bearing diagnostics printed')
        model = Path(result.stdout.strip().splitlines()[-1].removeprefix('path='))
    model = model.resolve()
    if model.stat().st_size != MODEL_BYTES or digest(model) != MODEL_SHA:
        raise SystemExit('Model size or SHA-256 does not match the pin')
    build = out / 'native'
    options = ['cmake', '-S', RUNTIME / 'native', '-B', build, '-DCMAKE_BUILD_TYPE=Release',
               '-DLLAMA_SOURCE_DIR=' + str(source.resolve())]
    if args.java_home: options.append('-DJAVA_HOME=' + str(args.java_home))
    run(options); run(['cmake', '--build', build, '--target', 'rose_composer', '-j', '6'])
    java = (Path(args.java_home) / 'bin/java') if args.java_home else Path(shutil.which('java') or '')
    javac = java.with_name('javac')
    sources = sorted(p for p in (RUNTIME.parent / 'java/src/rose/models/composer').glob('*.java') if p.name != 'ScriptedInference.java')
    source_key = hashlib.sha256(''.join(digest(p) for p in sources).encode()).hexdigest()[:16]
    classes = out / 'classes' / source_key; classes.mkdir(parents=True, exist_ok=True)
    run([javac, '--release', '8', '-encoding', 'UTF-8', '-Xlint:all,-options', '-Werror', '-d', classes, *sources])
    jar = out / 'composer.jar'
    run([java.with_name('jar'), '--create', '--file', jar, '-C', classes, '.'])
    command = [str(java), '-Xms16m', '-Xmx256m', '-Djava.library.path=' + str(build), '-cp', str(jar),
               'rose.models.composer.ComposerWorker', str(model)]
    receipt = {'model': 'Qwen/Qwen3-1.7B-GGUF', 'modelRevision': MODEL_REV, 'modelSha256': MODEL_SHA,
               'runtimeRevision': LLAMA_REV, 'command': command,
               'artifacts': {p.name: digest(p) for p in [jar, *build.glob('librose_composer.*')] if p.is_file()},
               'sourceRevision': run(['git', '-C', ROOT, 'rev-parse', 'HEAD'], stdout=subprocess.PIPE, text=True).stdout.strip(),
               'bindingSources': {str(p.relative_to(ROOT)): digest(p) for p in [*sources, RUNTIME / 'native/rose_composer.cpp', RUNTIME / 'native/CMakeLists.txt']}}
    receipt['tools'] = {name: run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout.splitlines()[0]
                        for name, command in [('java', [java, '-version']), ('cmake', ['cmake', '--version']), ('cxx', [os.environ.get('CXX', 'c++'), '--version'])]}
    shutil.copyfile(source / 'LICENSE', out / 'llama.cpp-LICENSE.txt')
    config = out / 'worker.json'; config.write_text(json.dumps(receipt, indent=2) + '\n')
    print('Prepared local worker: ' + str(config))
    print('Discovered at the default path on the next authorized Rose start, or select with ROSE_WORKSPACE_MODEL_CONFIG.')

if __name__ == '__main__': main()
