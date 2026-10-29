#!/usr/bin/env python3
"""APP-HIFI-QA-1029-Q1: sample the rendered HiFi Rose sphere from prototype screenshots and print the native
RosePresenceView gradient stops computed from source constants (RosePresenceModel at main 77878c5).
Prototype pixels only; native values are source-derived, not rendered Android pixels.
Usage: sample-sphere.py SHOT_DIR"""
import sys
from PIL import Image
S=sys.argv[1]
mix=lambda a,b,t:tuple(round(a[i]+(b[i]-a[i])*t) for i in range(3))
hx=lambda c:'#%02x%02x%02x'%c
for name,base in [('ROSE',(0xf2,0x7a,0x87)),('SILVER',(0xa6,0xba,0xcf))]:
    print('native',name,'stops light/base/dark/base:',hx(mix(base,(255,255,255),.55)),hx(base),hx(mix(base,(0x1a,0x2a,0x36),.45)),hx(base))
def sphere(f,cx,cy,r):
    im=Image.open(f'{S}/{f}').convert('RGB')
    px=sorted((im.getpixel((x,y)) for y in range(cy-r,cy+r) for x in range(cx-r,cx+r) if (x-cx)**2+(y-cy)**2<(r*.8)**2),key=sum)
    print(f,'HiFi sphere median',hx(px[len(px)//2]),'p5',hx(px[len(px)//20]),'p95',hx(px[-len(px)//20]))
sphere('05-rose-connecting.png',204,374,58)
sphere('06-rose-speaking-result.png',75,182,24)
sphere('07-rose-muted-speaking.png',75,182,24)
