package dev.pixel.voice;
/** Display-pixel tap constrained to an observed, clipped surface. */
final class TapPoint {
 static int[] checked(int[] area,int x,int y){
  if(area.length!=4||x<area[0]||y<area[1]||x>=area[2]||y>=area[3])throw new IllegalArgumentException("Tap must stay inside the visible target");
  return new int[]{x,y};
 }
}
