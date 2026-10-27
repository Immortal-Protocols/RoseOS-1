/*
 * Android status-bar adaptation of Libraries.dev Voice Glow.
 * Lobe layout and source-over ellipse composition adapted from VoiceGlowKit
 * and voice-glow/src/styles.ts, Copyright (c) 2026 Jakub Antalik, MIT.
 * Android integration: Apache-2.0.
 * App-side copy of os/overlays/.../wm/shell/StatusVoiceGlow.java for stock
 * Android; keep the shader identical to the OS version.
 *
 * MIT License
 *
 * Copyright (c) 2026 Jakub Antalik
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */
package dev.pixel.voice;

import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RuntimeShader;

/** Two independent, audio-level-driven glows. No microphone or network access. Requires API 33. */
final class StatusVoiceGlow {
 private final RuntimeShader shader=new RuntimeShader(SOURCE);
 private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
 private float envelope;

 void draw(Canvas canvas,float x,float y,float width,float height,float level,float seconds,float elapsedMs,boolean orange){
  float target=(float)Math.sqrt(Math.max(0,Math.min(1,level)));
  float tau=target>envelope?45f:190f;
  envelope+=(target-envelope)*(1f-(float)Math.exp(-elapsedMs/tau));
  shader.setFloatUniform("size",width,height);
  shader.setFloatUniform("voice",envelope);
  shader.setFloatUniform("clock",seconds);
  shader.setFloatUniform("warm",orange?1f:0f);
  paint.setShader(shader);
  canvas.save();canvas.translate(x,y);canvas.drawRect(0,0,width,height,paint);canvas.restore();
  paint.setShader(null);
 }

 void clear(){envelope=0;}

 private static final String SOURCE=
  "uniform float2 size;\n"+
  "uniform float voice;\n"+
  "uniform float clock;\n"+
  "uniform float warm;\n"+
  "float4 over(float4 src, float4 dst) {\n"+
  "    return src + dst * (1.0 - src.a);\n"+
  "}\n"+
  "float4 lobe(float2 p, float cx, float rx, float ry, float3 color, float alpha) {\n"+
  "    float2 q = (p - float2(cx, -.04)) / float2(rx, ry);\n"+
  "    float a = alpha * pow(max(0.0, 1.0 - length(q)), 1.55);\n"+
  "    return float4(color * a, a);\n"+
  "}\n"+
  "half4 main(float2 xy) {\n"+
  "    float u = mix(1.0-xy.x/size.x,xy.x/size.x,warm);\n"+
  "    float v = xy.y/size.y;\n"+
  "    float2 p = float2(u,v);\n"+
  "    float depth = .09 + voice * 1.45;\n"+
  "    float drift = sin(clock*1.35)*voice*.075;\n"+
  "    float3 deep = mix(float3(.06,.24,.95),float3(1.0,.16,.025),warm);\n"+
  "    float3 color = mix(float3(.035,.57,1.0),float3(1.0,.44,.045),warm);\n"+
  "    float3 bright = mix(float3(.15,.86,1.0),float3(1.0,.74,.18),warm);\n"+
  "    float4 c = float4(0.0);\n"+
  "    c=over(lobe(p,-.06+drift,.70,depth*.90,deep,.80),c);\n"+
  "    c=over(lobe(p,.16-drift,.49,depth*1.12,color,.86),c);\n"+
  "    c=over(lobe(p,.37+drift,.43,depth*.85,bright,.76),c);\n"+
  "    c=over(lobe(p,.57-drift,.39,depth*1.02,color,.76),c);\n"+
  "    c=over(lobe(p,.76+drift,.35,depth*.73,deep,.66),c);\n"+
  "    c=over(lobe(p,.95-drift,.32,depth*.65,color,.62),c);\n"+
  "    c=over(lobe(p,.04,.42,depth*.66,bright,.50),c);\n"+
  "    float side = exp(-u/(.035+voice*.20))\n"+
  "            * pow(max(0.0,1.0-v),1.4)*(.14+voice*.45);\n"+
  "    c=over(float4(color*side,side),c);\n"+
  "    float bottom = 1.0-smoothstep(.82,1.0,v);\n"+
  "    float camera = 1.0-smoothstep(.84,1.0,u);\n"+
  "    return half4(c * bottom * camera);\n"+
  "}\n";
}
