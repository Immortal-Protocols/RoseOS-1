package dev.pixel.voice;

import java.util.List;

/**
 * Computer Link (docs/COMPUTER-LINK.md): the HID reports this phone sends to an attached computer over
 * Bluetooth. One combined device: a keyboard (report 1) and an absolute pointer (report 2) whose X and Y
 * are fractions of the whole screen, so a point picked on the camera frame lands on the same point of
 * the computer's screen regardless of pointer acceleration. Pure Java: no Android types, unit-tested.
 * Text maps through a US (ANSI) layout; anything outside printable ASCII, tab and newline is refused.
 */
final class ComputerHid {
 private ComputerHid(){}
 static final int KEYBOARD_ID=1,POINTER_ID=2;
 static final int AXIS_MAX=32767;
 static final int MOD_CTRL=0x01,MOD_SHIFT=0x02,MOD_ALT=0x04,MOD_GUI=0x08;
 static final int BUTTON_LEFT=1,BUTTON_RIGHT=2,BUTTON_MIDDLE=4;

 static final byte[] DESCRIPTOR=bytes(
  // Keyboard, report 1: modifiers, reserved, LED output, six key slots.
  0x05,0x01,0x09,0x06,0xA1,0x01,0x85,KEYBOARD_ID,
  0x05,0x07,0x19,0xE0,0x29,0xE7,0x15,0x00,0x25,0x01,0x75,0x01,0x95,0x08,0x81,0x02,
  0x95,0x01,0x75,0x08,0x81,0x01,
  0x95,0x05,0x75,0x01,0x05,0x08,0x19,0x01,0x29,0x05,0x91,0x02,
  0x95,0x01,0x75,0x03,0x91,0x01,
  0x95,0x06,0x75,0x08,0x15,0x00,0x25,0x65,0x05,0x07,0x19,0x00,0x29,0x65,0x81,0x00,
  0xC0,
  // Absolute pointer, report 2: three buttons, X and Y 0..32767, vertical wheel, horizontal pan.
  0x05,0x01,0x09,0x02,0xA1,0x01,0x85,POINTER_ID,0x09,0x01,0xA1,0x00,
  0x05,0x09,0x19,0x01,0x29,0x03,0x15,0x00,0x25,0x01,0x95,0x03,0x75,0x01,0x81,0x02,
  0x95,0x01,0x75,0x05,0x81,0x01,
  0x05,0x01,0x09,0x30,0x09,0x31,0x16,0x00,0x00,0x26,0xFF,0x7F,0x75,0x10,0x95,0x02,0x81,0x02,
  0x09,0x38,0x15,0x81,0x25,0x7F,0x75,0x08,0x95,0x01,0x81,0x06,
  0x05,0x0C,0x0A,0x38,0x02,0x15,0x81,0x25,0x7F,0x75,0x08,0x95,0x01,0x81,0x06,
  0xC0,0xC0);

 private static byte[] bytes(int... values){byte[] out=new byte[values.length];for(int i=0;i<values.length;i++)out[i]=(byte)values[i];return out;}

 /** Keyboard report payload (without the report ID): modifiers, reserved, up to six key usages. */
 static byte[] keyboard(int modifiers,int... usages){
  if(usages.length>6)throw new IllegalArgumentException("At most six keys at once");
  byte[] out=new byte[8];out[0]=(byte)modifiers;
  for(int i=0;i<usages.length;i++)out[2+i]=(byte)usages[i];
  return out;
 }

 /** Pointer report payload: buttons, X and Y (little-endian, 0..AXIS_MAX), wheel, pan. */
 static byte[] pointer(int buttons,int x,int y,int wheel,int pan){
  if(x<0||x>AXIS_MAX||y<0||y>AXIS_MAX)throw new IllegalArgumentException("Pointer position out of range");
  if(wheel<-127||wheel>127||pan<-127||pan>127)throw new IllegalArgumentException("Scroll step out of range");
  return new byte[]{(byte)buttons,(byte)x,(byte)(x>>8),(byte)y,(byte)(y>>8),(byte)wheel,(byte)pan};
 }

 /** The pointer axis value for the centre of pixel px of a frame size pixels wide. */
 static int axis(int px,int size){
  if(size<=0||px<0||px>=size)throw new IllegalArgumentException("Point outside the frame");
  return (int)Math.round((px+0.5)*AXIS_MAX/size);
 }

 /** {modifiers, usage} for one character on a US layout, or null when it cannot be typed. */
 static int[] character(char c){
  if(c>='a'&&c<='z')return new int[]{0,0x04+(c-'a')};
  if(c>='A'&&c<='Z')return new int[]{MOD_SHIFT,0x04+(c-'A')};
  if(c>='1'&&c<='9')return new int[]{0,0x1E+(c-'1')};
  if(c=='0')return new int[]{0,0x27};
  switch(c){
   case '\n':return new int[]{0,0x28};
   case '\t':return new int[]{0,0x2B};
   case ' ':return new int[]{0,0x2C};
   case '!':return new int[]{MOD_SHIFT,0x1E};
   case '@':return new int[]{MOD_SHIFT,0x1F};
   case '#':return new int[]{MOD_SHIFT,0x20};
   case '$':return new int[]{MOD_SHIFT,0x21};
   case '%':return new int[]{MOD_SHIFT,0x22};
   case '^':return new int[]{MOD_SHIFT,0x23};
   case '&':return new int[]{MOD_SHIFT,0x24};
   case '*':return new int[]{MOD_SHIFT,0x25};
   case '(':return new int[]{MOD_SHIFT,0x26};
   case ')':return new int[]{MOD_SHIFT,0x27};
   case '-':return new int[]{0,0x2D};case '_':return new int[]{MOD_SHIFT,0x2D};
   case '=':return new int[]{0,0x2E};case '+':return new int[]{MOD_SHIFT,0x2E};
   case '[':return new int[]{0,0x2F};case '{':return new int[]{MOD_SHIFT,0x2F};
   case ']':return new int[]{0,0x30};case '}':return new int[]{MOD_SHIFT,0x30};
   case '\\':return new int[]{0,0x31};case '|':return new int[]{MOD_SHIFT,0x31};
   case ';':return new int[]{0,0x33};case ':':return new int[]{MOD_SHIFT,0x33};
   case '\'':return new int[]{0,0x34};case '"':return new int[]{MOD_SHIFT,0x34};
   case '`':return new int[]{0,0x35};case '~':return new int[]{MOD_SHIFT,0x35};
   case ',':return new int[]{0,0x36};case '<':return new int[]{MOD_SHIFT,0x36};
   case '.':return new int[]{0,0x37};case '>':return new int[]{MOD_SHIFT,0x37};
   case '/':return new int[]{0,0x38};case '?':return new int[]{MOD_SHIFT,0x38};
   default:return null;
  }
 }

 /** Index of the first character that cannot be typed, or -1. */
 static int untypable(String text){
  for(int i=0;i<text.length();i++)if(character(text.charAt(i))==null)return i;
  return -1;
 }

 static int modifier(String name){
  switch(name){case "ctrl":return MOD_CTRL;case "shift":return MOD_SHIFT;case "alt":return MOD_ALT;case "cmd":return MOD_GUI;default:return 0;}
 }

 static int key(String name){
  switch(name){
   case "enter":return 0x28;case "escape":return 0x29;case "backspace":return 0x2A;case "tab":return 0x2B;case "space":return 0x2C;
   case "delete":return 0x4C;case "right":return 0x4F;case "left":return 0x50;case "down":return 0x51;case "up":return 0x52;
   case "home":return 0x4A;case "pageup":return 0x4B;case "end":return 0x4D;case "pagedown":return 0x4E;
  }
  if(name.length()==1){char c=name.charAt(0);if(c>='a'&&c<='z'||c>='0'&&c<='9')return character(c)[1];}
  if(name.matches("f([1-9]|1[0-2])"))return 0x3A+Integer.parseInt(name.substring(1))-1;
  return 0;
 }

 /** The pressed report for a chord such as [cmd, shift, 4]: modifier bits plus one to six keys. */
 static byte[] chord(List<String> names){
  int modifiers=0,count=0;int[] keys=new int[6];
  for(String name:names){
   int mod=modifier(name);
   if(mod!=0){modifiers|=mod;continue;}
   int usage=key(name);
   if(usage==0)throw new IllegalArgumentException("Unsupported key: "+name);
   if(count==6)throw new IllegalArgumentException("At most six keys at once");
   keys[count++]=usage;
  }
  int[] used=new int[count];System.arraycopy(keys,0,used,0,count);
  return keyboard(modifiers,used);
 }
}
