package dev.pixel.voice;

import java.util.Arrays;

/** Computer Link HID reports, US key map and frame geometry (docs/COMPUTER-LINK.md). No device needed. */
public final class ComputerLinkTest {
 public static void main(String[] args){
  descriptor();keys();pointer();geometry();
  System.out.println("PASS: Computer Link HID descriptor, US key map, absolute pointer and frame geometry");
 }

 static void descriptor(){
  byte[] d=ComputerHid.DESCRIPTOR;
  int open=0;
  for(int i=0;i<d.length;){
   int prefix=d[i]&0xFF,size=prefix&3;if(size==3)size=4;
   if(prefix==0xA1)open++;
   if(prefix==0xC0)open--;
   check(open>=0,"collections balance");
   i+=1+size;
   check(i<=d.length,"items fit");
  }
  check(open==0,"every collection closes");
  check(contains(d,0x85,ComputerHid.KEYBOARD_ID)&&contains(d,0x85,ComputerHid.POINTER_ID),"both report IDs declared");
  // X and Y are absolute (Input Data,Var,Abs = 0x02) with logical maximum 0x7FFF.
  check(contains(d,0x26,0xFF,0x7F,0x75,0x10,0x95,0x02,0x81,0x02),"absolute 16-bit X/Y");
 }

 static void keys(){
  eq(ComputerHid.character('a'),0,0x04);eq(ComputerHid.character('Z'),ComputerHid.MOD_SHIFT,0x1D);
  eq(ComputerHid.character('1'),0,0x1E);eq(ComputerHid.character('0'),0,0x27);
  eq(ComputerHid.character('@'),ComputerHid.MOD_SHIFT,0x1F);eq(ComputerHid.character('?'),ComputerHid.MOD_SHIFT,0x38);
  eq(ComputerHid.character('\n'),0,0x28);eq(ComputerHid.character(' '),0,0x2C);
  check(ComputerHid.character('é')==null&&ComputerHid.character('\u0007')==null,"non-ASCII and control refused");
  // Every printable ASCII character is typable, with a distinct key/modifier pair.
  java.util.Set<String> seen=new java.util.HashSet<>();
  for(char c=32;c<127;c++){int[] k=ComputerHid.character(c);check(k!=null,"typable "+c);check(seen.add(k[0]+":"+k[1]),"distinct "+c);}
  check(ComputerHid.untypable("Hello, world!\n")==-1,"plain text");
  check(ComputerHid.untypable("café")==3,"first untypable index");
  check(Arrays.equals(ComputerHid.chord(Arrays.asList("cmd","space")),new byte[]{0x08,0,0x2C,0,0,0,0,0}),"cmd+space");
  check(Arrays.equals(ComputerHid.chord(Arrays.asList("cmd","shift","4")),new byte[]{0x0A,0,0x21,0,0,0,0,0}),"cmd+shift+4");
  check(ComputerHid.key("f12")==0x45&&ComputerHid.key("f1")==0x3A&&ComputerHid.key("f13")==0,"function keys");
  rejects(()->ComputerHid.chord(Arrays.asList("cmd","power")));
  check(Arrays.equals(ComputerHid.keyboard(0),new byte[8]),"release is all zero");
 }

 static void pointer(){
  check(ComputerHid.axis(0,1920)==9&&ComputerHid.axis(1919,1920)==32758,"axis ends stay inside the screen");
  check(ComputerHid.axis(960,1920)==16392,"axis centre");
  rejects(()->ComputerHid.axis(1920,1920));rejects(()->ComputerHid.axis(-1,1920));
  check(Arrays.equals(ComputerHid.pointer(1,0x1234,0x7FFF,-1,2),new byte[]{1,0x34,0x12,(byte)0xFF,0x7F,(byte)0xFF,2}),"pointer payload");
  rejects(()->ComputerHid.pointer(0,32768,0,0,0));rejects(()->ComputerHid.pointer(0,0,0,128,0));
 }

 static void geometry(){
  // A 16:9 monitor seen at a slight angle still snaps to 16:9.
  check(Arrays.equals(ComputerGeometry.frameSize(new float[]{100,120,1700,80,1720,1000,90,960}),new int[]{1920,1080}),"16:9 snap");
  check(Arrays.equals(ComputerGeometry.frameSize(new float[]{0,0,1600,0,1600,1000,0,1000}),new int[]{1920,1200}),"16:10");
  rejects(()->ComputerGeometry.frameSize(new float[]{0,0,1600,1000,1600,0,0,1000}));// crossed (bow-tie)
  rejects(()->ComputerGeometry.frameSize(new float[]{0,0,5,0,5,5,0,5}));// too small
  rejects(()->ComputerGeometry.frameSize(new float[]{0,0,1,1}));
 }

 static boolean contains(byte[] d,int... seq){
  outer:for(int i=0;i+seq.length<=d.length;i++){for(int j=0;j<seq.length;j++)if((d[i+j]&0xFF)!=seq[j])continue outer;return true;}
  return false;
 }
 static void eq(int[] actual,int mod,int usage){check(actual!=null&&actual[0]==mod&&actual[1]==usage,"key "+mod+":"+usage+" got "+Arrays.toString(actual));}
 static void check(boolean ok,String what){if(!ok)throw new AssertionError(what);}
 static void rejects(Runnable r){try{r.run();}catch(IllegalArgumentException ok){return;}throw new AssertionError("Accepted invalid input");}
}
