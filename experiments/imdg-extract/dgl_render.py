"""Read-only PDF page rasterization using macOS CoreGraphics (no extra packages)."""
import ctypes as C,sys
from pathlib import Path
cg=C.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
cf=C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
io=C.CDLL('/System/Library/Frameworks/ImageIO.framework/ImageIO')
class Point(C.Structure): _fields_=[('x',C.c_double),('y',C.c_double)]
class Size(C.Structure): _fields_=[('width',C.c_double),('height',C.c_double)]
class Rect(C.Structure): _fields_=[('origin',Point),('size',Size)]
def bind(lib,name,result,args):
 f=getattr(lib,name);f.restype=result;f.argtypes=args;return f
ptr=C.c_void_p;num=C.c_double
url=bind(cf,'CFURLCreateFromFileSystemRepresentation',ptr,[ptr,C.c_char_p,C.c_long,C.c_bool])
string=bind(cf,'CFStringCreateWithCString',ptr,[ptr,C.c_char_p,C.c_uint])
path=str(Path(sys.argv[1]).resolve()).encode();u=url(None,path,len(path),False)
doc=bind(cg,'CGPDFDocumentCreateWithURL',ptr,[ptr])(u)
pagefunc=bind(cg,'CGPDFDocumentGetPage',ptr,[ptr,C.c_size_t]);boxfunc=bind(cg,'CGPDFPageGetBoxRect',Rect,[ptr,C.c_int])
space=bind(cg,'CGColorSpaceCreateDeviceRGB',ptr,[])()
ctxfunc=bind(cg,'CGBitmapContextCreate',ptr,[ptr,C.c_size_t,C.c_size_t,C.c_size_t,C.c_size_t,ptr,C.c_uint])
scale=bind(cg,'CGContextScaleCTM',None,[ptr,num,num]);draw=bind(cg,'CGContextDrawPDFPage',None,[ptr,ptr]);image=bind(cg,'CGBitmapContextCreateImage',ptr,[ptr])
destfunc=bind(io,'CGImageDestinationCreateWithURL',ptr,[ptr,ptr,C.c_size_t,ptr]);add=bind(io,'CGImageDestinationAddImage',None,[ptr,ptr,ptr]);finish=bind(io,'CGImageDestinationFinalize',C.c_bool,[ptr]);release=bind(cf,'CFRelease',None,[ptr])
for n in map(int,sys.argv[2:]):
 p=pagefunc(doc,n);b=boxfunc(p,0);s=2.5;w=int(b.size.width*s);h=int(b.size.height*s)
 ctx=ctxfunc(None,w,h,8,w*4,space,1);bind(cg,'CGContextSetRGBFillColor',None,[ptr,num,num,num,num])(ctx,1,1,1,1);bind(cg,'CGContextFillRect',None,[ptr,Rect])(ctx,Rect(Point(0,0),Size(w,h)))
 scale(ctx,s,s);draw(ctx,p);im=image(ctx)
 target=f'/private/tmp/dgl-page-{n}.png'.encode();dest=destfunc(url(None,target,len(target),False),string(None,b'public.png',0x08000100),1,None);add(dest,im,None);assert finish(dest)
 for obj in (dest,im,ctx):release(obj)
