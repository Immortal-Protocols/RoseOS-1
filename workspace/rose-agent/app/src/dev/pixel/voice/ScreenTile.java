package dev.pixel.voice;

import android.content.ComponentName;
import android.content.Context;
import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;

/** Quick Settings switch between "keep screen on" and "allow screen off" for Rose Agent. */
public final class ScreenTile extends TileService {
 @Override public void onStartListening(){render();}
 @Override public void onClick(){ScreenPolicy.setKeepScreenOn(this,!ScreenPolicy.keepScreenOn(this));render();}
 void render(){
  Tile tile=getQsTile();if(tile==null)return;boolean on=ScreenPolicy.keepScreenOn(this);
  tile.setState(on?Tile.STATE_ACTIVE:Tile.STATE_INACTIVE);tile.setLabel("Rose Agent screen");tile.setSubtitle(on?"Keep on":"Allow off");
  tile.setContentDescription(on?"Rose Agent keeps the screen on":"Rose Agent allows the screen to turn off");tile.updateTile();
 }
 static void refresh(Context c){try{TileService.requestListeningState(c,new ComponentName(c,ScreenTile.class));}catch(RuntimeException ignored){}}
}
