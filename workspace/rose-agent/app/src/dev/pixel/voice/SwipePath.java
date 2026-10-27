package dev.pixel.voice;

final class SwipePath {
 static float[] horizontal(int[] visible,int[] requested,String direction){
  if(!direction.equals("left")&&!direction.equals("right"))throw new IllegalArgumentException("Invalid swipe direction");
  if(requested.length!=4||requested[0]<visible[0]||requested[1]<visible[1]||requested[2]>visible[2]||requested[3]>visible[3]
    ||requested[2]-requested[0]<48||requested[3]-requested[1]<24)throw new IllegalArgumentException("Swipe region must be inside the visible target");
  float width=requested[2]-requested[0],left=requested[0]+width*.2f,right=requested[2]-width*.2f,y=(requested[1]+requested[3])/2f;
  return direction.equals("left")?new float[]{right,y,left,y}:new float[]{left,y,right,y};
 }
}
