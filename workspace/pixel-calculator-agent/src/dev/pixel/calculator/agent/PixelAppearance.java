package dev.pixel.calculator.agent;
import android.app.Activity;
import android.content.res.Configuration;
import android.content.res.Resources;
import android.view.View;
final class PixelAppearance {
 private static Resources resources;
 static boolean dark(){Resources r=resources==null?Resources.getSystem():resources;return (r.getConfiguration().uiMode&Configuration.UI_MODE_NIGHT_MASK)==Configuration.UI_MODE_NIGHT_YES;}
 static void attach(Activity activity){resources=activity.getResources();activity.setTheme(dark()?android.R.style.Theme_Material_NoActionBar:android.R.style.Theme_Material_Light_NoActionBar);}
 static int color(int value){if(!dark())return value;int alpha=value&0xff000000;switch(value&0xffffff){
 case 0xb9dff3:return alpha|0x141e33;
 case 0xfff7e5:return alpha|0x24324b;
 case 0x29385d:return alpha|0xeaf2ff;
 case 0x576f90:return alpha|0xb1c2da;
 case 0x3f567a:return alpha|0x91b9e3;
 case 0xd5e7f2:return alpha|0x2d405d;
 case 0xb3d0e4:return alpha|0x435d7c;
 case 0xb3d4e9:return alpha|0x304b6a;
 case 0xeaf3f8:return alpha|0x172237;
 case 0x789ab5:return alpha|0x8baacb;
 case 0xe1edf4:return alpha|0x172339;
 case 0xdcecf5:return alpha|0x20304b;
 case 0xc7e0ef:return alpha|0x1c2c47;
 case 0x83abc8:return alpha|0x567da3;
 case 0x8cc5e3:return alpha|0x456f9d;
 case 0xfff0d6:return alpha|0x25334a;
 case 0xf2e3c8:return alpha|0x35425a;
 case 0xdddcd8:return alpha|0x43536b;
 case 0xf9f4da:return alpha|0xeceddc;
 case 0xe0edf5:return alpha|0x18273e;
 case 0x923f38:return alpha|0xffc0b4;
 case 0xffdace:return alpha|0x603f49;
 case 0xd6a896:return alpha|0x382d3c;
 case 0xffc0af:return alpha|0xb6776d;
 case 0xf58b78:return alpha|0xf29483;
 case 0x3f6497:return alpha|0x416b9f;
 case 0xcf665a:return alpha|0xf29483;
 case 0x816b4e:return alpha|0xd4b88e;
 case 0xbd683f:return alpha|0xefa786;
 case 0xbd6146:return alpha|0xf2a38e;
 case 0x9b5c20:return alpha|0xe2b176;
 case 0xa87835:return alpha|0xe8c177;
 case 0x527181:return alpha|0x9cbed1;
 default:return value;}}
 static void bars(Activity activity){int flags=activity.getWindow().getDecorView().getSystemUiVisibility();int light=View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR|View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;activity.getWindow().getDecorView().setSystemUiVisibility(dark()?flags&~light:flags|light);}
}
