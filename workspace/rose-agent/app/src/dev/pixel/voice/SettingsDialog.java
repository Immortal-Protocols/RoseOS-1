package dev.pixel.voice;

import android.app.Activity;
import android.app.AlertDialog;
import android.view.Menu;
import android.view.MenuItem;
import java.util.ArrayList;

/** Settings must not depend on the position or attachment of a full-screen Home popup anchor. */
final class SettingsDialog {
 static MenuItem add(Menu menu,String title){return menu.add(0,menu.size()+1,0,title);}

 static AlertDialog show(Activity activity, Menu menu) {
  ArrayList<MenuItem> items=new ArrayList<>();
  ArrayList<String> labels=new ArrayList<>();
  for(int n=0;n<menu.size();n++) {
   MenuItem item=menu.getItem(n);
   if(!item.isVisible())continue;
   items.add(item);
   labels.add(item.getTitle()+(item.isCheckable()?(item.isChecked()?" — On":" — Off"):""));
  }
  return new AlertDialog.Builder(activity).setTitle("Rose Agent settings")
   .setItems(labels.toArray(new String[0]),(dialog,position)->{
    MenuItem item=items.get(position);
    if(item.isEnabled())menu.performIdentifierAction(item.getItemId(),0);
   }).setNegativeButton("Close",null).show();
 }
}
