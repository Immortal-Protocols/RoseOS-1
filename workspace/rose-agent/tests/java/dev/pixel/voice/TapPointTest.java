package dev.pixel.voice;
public class TapPointTest {
 public static void main(String[] args){
  int[] f={10,20,100,200};int[] p=TapPoint.checked(f,10,20);
  if(p[0]!=10||p[1]!=20)throw new AssertionError();
  TapPoint.checked(f,99,199);
  reject(f,9,20);reject(f,10,19);reject(f,100,20);reject(f,10,200);
  reject(new int[]{10,20,10,20},10,20);
  System.out.println("PASS: point tap clips all edges and rejects invisible areas");
 }
 static void reject(int[] f,int x,int y){try{TapPoint.checked(f,x,y);}catch(IllegalArgumentException ok){return;}throw new AssertionError();}
}
