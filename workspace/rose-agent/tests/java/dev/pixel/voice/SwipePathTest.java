package dev.pixel.voice;
public final class SwipePathTest {
 public static void main(String[] args){
  int[] visible={0,300,1080,2200},row={20,1250,1060,1420};
  float[] left=SwipePath.horizontal(visible,row,"left"),right=SwipePath.horizontal(visible,row,"right");
  if(left[0]<=left[2]||right[0]>=right[2]||left[1]!=left[3]||left[1]<1250||left[1]>1420)throw new AssertionError();
  reject(visible,new int[]{0,200,1000,400},"left");reject(visible,new int[]{0,500,2000,700},"left");reject(visible,new int[]{20,500,21,510},"left");reject(visible,row,"down");
  System.out.println("PASS: horizontal gesture directions, fixed row height and off-target rejection");
 }
 static void reject(int[] v,int[] r,String d){try{SwipePath.horizontal(v,r,d);}catch(IllegalArgumentException ok){return;}throw new AssertionError("Unsafe region accepted");}
}
