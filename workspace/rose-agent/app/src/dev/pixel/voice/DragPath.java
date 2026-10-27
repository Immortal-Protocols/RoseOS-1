package dev.pixel.voice;

/** One bounded finger movement on an observed surface. */
final class DragPath {
 static float[] checked(int[] visible,int[] points,int duration){
  if(points.length!=4||duration<200||duration>2000)throw new IllegalArgumentException("Invalid drag duration or points");
  for(int i=0;i<4;i+=2)if(points[i]<visible[0]||points[i]>=visible[2]||points[i+1]<visible[1]||points[i+1]>=visible[3])throw new IllegalArgumentException("Drag must stay inside the visible target");
  if(points[0]==points[2]&&points[1]==points[3])throw new IllegalArgumentException("Drag endpoints must differ");
  return new float[]{points[0],points[1],points[2],points[3]};
 }
}
