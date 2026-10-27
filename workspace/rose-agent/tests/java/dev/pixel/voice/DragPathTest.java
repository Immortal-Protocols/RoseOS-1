package dev.pixel.voice;
public class DragPathTest {
 public static void main(String[] args){
  int[] frame={10,20,1000,2000};float[] p=DragPath.checked(frame,new int[]{20,30,900,1800},600);
  if(p[0]!=20||p[1]!=30||p[2]!=900||p[3]!=1800)throw new AssertionError();
  reject(frame,new int[]{0,30,900,1800},600);reject(frame,new int[]{20,30,1000,1800},600);
  reject(frame,new int[]{20,30,20,30},600);reject(frame,new int[]{20,30,90,180},199);
  reject(frame,new int[]{20,30,90,180},2001);
  System.out.println("PASS: drag endpoints, visible bounds and duration");
 }
 static void reject(int[] f,int[] p,int d){try{DragPath.checked(f,p,d);}catch(IllegalArgumentException ok){return;}throw new AssertionError();}
}
