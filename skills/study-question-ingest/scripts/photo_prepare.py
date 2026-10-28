"""On-device macOS Vision photo orientation, deskew and whitespace trimming."""
import base64,hashlib,json,re,shutil,subprocess,sys,tempfile,threading
from pathlib import Path
ROOT=Path(__file__).resolve().parent
_BUILD=threading.Lock();_LIMIT=threading.BoundedSemaphore(2)
def executable(runtime_dir=None):
    if sys.platform!='darwin':raise ValueError('本机暂不支持自动摆正，请使用手动旋转和框选')
    source=ROOT.parent/'assets/photo_prepare.swift';cache=Path(runtime_dir) if runtime_dir is not None else Path(tempfile.gettempdir())/('study-photo-'+str(__import__('os').getuid()));cache.mkdir(mode=0o700,parents=True,exist_ok=True);binary=cache/'photo-prepare';mark=cache/'source.sha256';digest=hashlib.sha256(source.read_bytes()).hexdigest()
    with _BUILD:
        if not binary.is_file() or not mark.is_file() or mark.read_text()!=digest:
            compiler=shutil.which('swiftc')
            if not compiler:raise ValueError('本机缺少照片处理组件，请先手动旋转和框选')
            cache.mkdir(mode=0o700,exist_ok=True)
            try:subprocess.run([compiler,'-O','-module-cache-path',str(cache/'modules'),str(source),'-o',str(binary)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180)
            except (OSError,subprocess.SubprocessError):raise ValueError('照片处理组件暂不可用，请手动核对方向与裁边')
            mark.write_text(digest)
    return binary

def prepare_photo(data,runtime_dir=None):
    if set(data)!={'data_url'} or not isinstance(data['data_url'],str) or len(data['data_url'])>23*1024**2:raise ValueError('请提供不超过16MB的JPG或PNG照片')
    match=re.fullmatch(r'data:image/(jpeg|png);base64,([A-Za-z0-9+/=\r\n]+)',data['data_url'])
    if not match:raise ValueError('照片格式不正确')
    try:raw=base64.b64decode(match[2],validate=True)
    except Exception:raise ValueError('照片数据不完整')
    if not raw or len(raw)>16*1024**2:raise ValueError('照片大小超限')
    if not (raw.startswith(b'\x89PNG\r\n\x1a\n') if match[1]=='png' else raw.startswith(b'\xff\xd8\xff')):raise ValueError('照片内容与格式不一致')
    binary=executable(runtime_dir)
    if not _LIMIT.acquire(timeout=5):raise ValueError('照片处理中，请稍后重试')
    try:
        with tempfile.TemporaryDirectory(prefix='study-photo-') as folder:
            source=Path(folder)/'source';output=Path(folder)/'processed.jpg';source.write_bytes(raw)
            try:r=subprocess.run([str(binary),str(source),str(output)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
            except (OSError,subprocess.SubprocessError):raise ValueError('照片自动处理未完成，原图保留，请手动核对方向和边缘')
            metadata=json.loads(r.stdout)
            if not output.is_file() or output.stat().st_size>16*1024**2:raise ValueError('处理后照片大小异常，请保留原图核对')
            metadata.update(data_url='data:image/jpeg;base64,'+base64.b64encode(output.read_bytes()).decode(),original_preserved=True,network_used=False)
            return metadata
    finally:_LIMIT.release()


def main():
    import argparse
    parser=argparse.ArgumentParser(description='本机自动摆正、轻微纠偏、外围裁边和压缩；原文件不修改。macOS需要swiftc。')
    parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if args.input.resolve() in (args.output.resolve(),args.report.resolve()) or args.output.resolve()==args.report.resolve():parser.error('输入、输出和回执必须分别保存')
    if args.output.exists() or args.report.exists():parser.error('输出或回执已存在，请换新文件名，不覆盖原资料')
    if not args.input.is_file() or args.input.stat().st_size>16*1024**2:parser.error('请选择不超过16MB的PNG/JPG')
    raw=args.input.read_bytes();mime='png' if raw.startswith(b'\x89PNG') else 'jpeg'
    try:
        result=prepare_photo({'data_url':'data:image/'+mime+';base64,'+base64.b64encode(raw).decode()})
        payload=base64.b64decode(result.pop('data_url').split(',')[1]);result.update(input_sha256=hashlib.sha256(raw).hexdigest(),output_sha256=hashlib.sha256(payload).hexdigest(),source_unchanged=True)
        with args.output.open('xb') as f:f.write(payload)
        with args.report.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
        print(json.dumps(result,ensure_ascii=False))
    except (ValueError,OSError) as error:parser.exit(1,str(error)+'\n')

if __name__=='__main__':main()
