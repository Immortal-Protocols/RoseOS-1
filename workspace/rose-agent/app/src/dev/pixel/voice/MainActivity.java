package dev.pixel.voice;
import android.app.*;import android.os.*;import android.content.*;import android.content.pm.*;import android.graphics.*;import android.graphics.drawable.GradientDrawable;import android.media.*;import android.view.*;import android.view.accessibility.AccessibilityNodeInfo;import android.widget.*;import java.util.*;import org.json.*;

/** Home interface only. Voice, glasses and delivery remain owned by VoiceService. */
public class MainActivity extends Activity {
 final Handler handler=new Handler();
 final int ink=PixelAppearance.color(0xff29385d),paper=PixelAppearance.color(0xffb9dff3),secondary=PixelAppearance.color(0xff576f90),green=PixelAppearance.color(0xff3f6497);
 LinearLayout page,voiceCenter;FrameLayout endSlot;TextView state,hint,route,task,connection,guardianName,taskModel;TaskStatusCard taskCard;Button end,send,guardianBack;
 EditText composer;MicControl micButton;boolean drawer=false;long seenTypedFinished=0;
 Typeface roundedFont;long startFeedbackUntil,endFeedbackUntil;String endingLabel="",lastFeedbackState="";
 boolean experimentalHome,chatMode=false,forceChatBottom=false,stickToBottom=true,userScrolling=false;long seenConversation=-2;ScrollView chatScroll,taskScroll;LinearLayout chatMessages;View breathingRoom;
 float swipeStartX,swipeStartY;boolean watchHomeSwipe,consumeHomeSwipe;
 LauncherPager launcherPager;LinearLayout homePageDots;int appColumns,appPerPage,homeColumns,appAreaWidth=-1,appAreaHeight=-1;float appCellHeight=-1;int packageGeneration;String builtHomeStamp;final ArrayList<TextView> pageDots=new ArrayList<>();
 ClockWidgetView clockWidget;WeatherWidgetView weatherWidget;GalleryWidgetView galleryWidget;int homePage=0,voicePage=1,widgetsPage=2,ourAppsPage=3;
 boolean launcherReceiverRegistered;RoseHomeView roseHome;HomeFrame homeFrame;HomeTray homeTray;boolean homeStopped;final HomeOverview homeOverview=new HomeOverview();
 /** Package changes only rebuild the app pages; they must not restart voice or reset the chat. */
 final BroadcastReceiver launcherPackages=new BroadcastReceiver(){@Override public void onReceive(Context context,Intent intent){packageGeneration++;if(launcherPager!=null&&!isFinishing())attachLauncherPages(true);}};
 /** Home visual card trial. false removes the card, its detail view and its Home options entry. */
 static final boolean VISUAL_CARDS=true;
 MacScreenPanel macPanel;FrameLayout screenDetail;boolean screenHostActive;
 FrameLayout visualSlot,visualDetail;LinearLayout visualDays,visualBody;Button visualPin,visualDismiss;JSONObject visualData;long seenVisual=-1;int visualDay;
 @Override public boolean dispatchTouchEvent(MotionEvent event){
  int action=event.getActionMasked();
  if(action==MotionEvent.ACTION_DOWN){
   consumeHomeSwipe=false;watchHomeSwipe=!drawer&&visualDetail==null&&screenDetail==null;swipeStartX=event.getX();swipeStartY=event.getY();
   WindowInsets insets=getWindow().getDecorView().getRootWindowInsets();
   if(insets!=null&&insets.isVisible(WindowInsets.Type.ime()))watchHomeSwipe=false;
   if(watchHomeSwipe&&macPanel!=null&&macPanel.isShown()){int[] pos=new int[2];macPanel.getLocationOnScreen(pos);if(event.getRawX()>=pos[0]&&event.getRawX()<pos[0]+macPanel.getWidth()&&event.getRawY()>=pos[1]&&event.getRawY()<pos[1]+macPanel.getHeight())watchHomeSwipe=false;}
   if(watchHomeSwipe&&composer!=null&&composer.isShown()){int[] pos=new int[2];composer.getLocationOnScreen(pos);if(event.getRawX()>=pos[0]&&event.getRawX()<pos[0]+composer.getWidth()&&event.getRawY()>=pos[1]&&event.getRawY()<pos[1]+composer.getHeight())watchHomeSwipe=false;}
   // The task area owns vertical gestures, including at the ends of its scroll range.
   if(watchHomeSwipe&&taskScroll!=null&&taskScroll.isShown()){int[] pos=new int[2];taskScroll.getLocationOnScreen(pos);if(event.getRawX()>=pos[0]&&event.getRawX()<pos[0]+taskScroll.getWidth()&&event.getRawY()>=pos[1]&&event.getRawY()<pos[1]+taskScroll.getHeight())watchHomeSwipe=false;}
  }
  if(consumeHomeSwipe){if(action==MotionEvent.ACTION_UP||action==MotionEvent.ACTION_CANCEL)consumeHomeSwipe=false;return true;}
  if(watchHomeSwipe&&action==MotionEvent.ACTION_MOVE){
   float up=swipeStartY-event.getY(),across=Math.abs(event.getX()-swipeStartX);
   if(up>=dp(72)&&up>across*1.5f){
    MotionEvent cancel=MotionEvent.obtain(event);cancel.setAction(MotionEvent.ACTION_CANCEL);super.dispatchTouchEvent(cancel);cancel.recycle();
    watchHomeSwipe=false;consumeHomeSwipe=true;apps();return true;
   }
  }
  if(action==MotionEvent.ACTION_UP||action==MotionEvent.ACTION_CANCEL)watchHomeSwipe=false;
  return super.dispatchTouchEvent(event);
 }
 int dp(float n){return (int)(n*getResources().getDisplayMetrics().density);}
 TextView text(String s,int size,int color){TextView t=new TextView(this);t.setText(s);t.setTextSize(size);t.setTextColor(color);t.setTypeface(Typeface.create("sans-serif-rounded",Typeface.NORMAL));return t;}
 void updateText(TextView view,String value){if(!value.contentEquals(view.getText()))view.setText(value);}
 GradientDrawable shape(int color,int radius){GradientDrawable d=new GradientDrawable();d.setColor(color);d.setCornerRadius(dp(radius));return d;}
 Button button(String label,Runnable click){Button b=new Button(this);b.setText(label);b.setMinHeight(0);b.setMinimumHeight(0);b.setStateListAnimator(null);b.setElevation(0);b.setAllCaps(false);b.setTextColor(ink);b.setTextSize(15);b.setBackground(raised(PixelAppearance.color(0xfffff7e5),PixelAppearance.color(0xffb3d0e4),20));b.setTypeface(Typeface.create("sans-serif-rounded",Typeface.BOLD));b.setOnClickListener(v->click.run());return b;}
 android.graphics.drawable.Drawable raised(int face,int edge,int radius){return PlayfulTheme.surface(face,edge,getResources().getDisplayMetrics().density,radius);}
 void command(String action){if("end".equals(action)){VoiceService s=VoiceService.instance;beginEnding(s==null||!s.live||!s.connected);startFeedbackUntil=0;}startForegroundService(new Intent(this,VoiceService.class).setAction(action));}
 void toggleVoice(){if(checkSelfPermission("android.permission.RECORD_AUDIO")!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{"android.permission.RECORD_AUDIO","android.permission.POST_NOTIFICATIONS"},1);return;}VoiceService s=VoiceService.instance;if(s!=null&&s.live&&s.connected)command("mute");else if(s==null||!s.communicating){startFeedbackUntil=SystemClock.uptimeMillis()+2000;renderVoice(false,true,false,"",0);command("talk");}}
 void sendTyped(){if(experimentalHome)openChat();VoiceService s=VoiceService.instance;if(s==null){Toast.makeText(this,"Still connecting. Try again shortly.",0).show();return;}if(s.submitText(composer.getText().toString().trim())){((android.view.inputmethod.InputMethodManager)getSystemService(INPUT_METHOD_SERVICE)).hideSoftInputFromWindow(composer.getWindowToken(),0);composer.clearFocus();refresh();}}
 void saveDraft(){if(composer!=null)getPreferences(0).edit().putString("typed-draft",composer.getText().toString()).apply();}
 boolean keepScreenOnEnabled(){return ScreenPolicy.keepScreenOn(this);}
 void setKeepScreenOnEnabled(boolean enabled){ScreenPolicy.setKeepScreenOn(this,enabled);ScreenPolicy.apply(this,screenHostActive);}
 // The Quick Settings tile can change the choice while this activity stays resumed under the shade.
 final android.content.SharedPreferences.OnSharedPreferenceChangeListener screenPolicyChanged=(prefs,key)->{if(ScreenPolicy.KEEP_ON.equals(key))ScreenPolicy.apply(this,screenHostActive);};
 void toggleLockedControl(){if(LockedControl.enabled(this)){LockedControl.disable(this);Toast.makeText(this,"App control while locked is off.",0).show();}else LockedControl.enable(this,()->{});}
 @Override public void onCreate(Bundle b){PixelAppearance.attach(this);super.onCreate(b);PixelAppearance.bars(this);setVolumeControlStream(AudioManager.STREAM_VOICE_CALL);getWindow().setStatusBarColor(paper);getWindow().setNavigationBarColor(paper);PixelAppearance.bars(this);startForegroundService(new Intent(this,VoiceService.class));home();showHomeFor(getIntent(),b==null);if(b!=null&&b.containsKey("home-page")&&launcherPager!=null)launcherPager.showPage(HomePages.restore(b.getInt("home-page"),launcherPager.getChildCount()),false);IntentFilter packages=new IntentFilter();packages.addAction(Intent.ACTION_PACKAGE_ADDED);packages.addAction(Intent.ACTION_PACKAGE_REMOVED);packages.addAction(Intent.ACTION_PACKAGE_CHANGED);packages.addAction(Intent.ACTION_PACKAGE_REPLACED);packages.addDataScheme("package");if(Build.VERSION.SDK_INT>=33)registerReceiver(launcherPackages,packages,Context.RECEIVER_EXPORTED);else registerReceiver(launcherPackages,packages);launcherReceiverRegistered=true;if(!DeviceIdentity.paired(this))startActivity(new Intent(this,PairingActivity.class));if(!PhoneEnvironment.permitted(this)&&!getSharedPreferences("location-context",0).getBoolean("asked",false))requestLocationContext();}
 void requestLocationContext(){getSharedPreferences("location-context",0).edit().putBoolean("asked",true).apply();requestPermissions(new String[]{"android.permission.ACCESS_COARSE_LOCATION","android.permission.ACCESS_FINE_LOCATION"},72);}
 void requestPhotoAccess(){if(Build.VERSION.SDK_INT>=34)requestPermissions(new String[]{"android.permission.READ_MEDIA_IMAGES","android.permission.READ_MEDIA_VISUAL_USER_SELECTED"},74);else if(Build.VERSION.SDK_INT>=33)requestPermissions(new String[]{"android.permission.READ_MEDIA_IMAGES"},74);else requestPermissions(new String[]{"android.permission.READ_EXTERNAL_STORAGE"},74);}
 @Override public void onRequestPermissionsResult(int r,String[] p,int[] g){super.onRequestPermissionsResult(r,p,g);if(r==74){if(galleryWidget!=null)galleryWidget.reload();return;}if(r==72){VoiceService s=VoiceService.instance;if(s!=null){s.notification(s.communicating);PhoneEnvironment.get(this).setActive(s.communicating);s.appWindowChanged();}return;}if(checkSelfPermission("android.permission.RECORD_AUDIO")==PackageManager.PERMISSION_GRANTED){startForegroundService(new Intent(this,VoiceService.class));command("talk");}}
 void base(){if(taskCard!=null)taskCard.dispose();destroyMacScreen();visualDetail=null;page=new LinearLayout(this);page.setOrientation(1);page.setBackground(new PlayfulTheme.World());page.setPadding(dp(24),dp(18),dp(24),dp(12));setContentView(page);page.setOnApplyWindowInsetsListener((v,i)->{v.setPadding(dp(24)+i.getSystemWindowInsetLeft(),dp(18)+i.getSystemWindowInsetTop(),dp(24)+i.getSystemWindowInsetRight(),dp(12)+i.getSystemWindowInsetBottom());if(!drawer&&voiceCenter!=null)voiceCenter.setVisibility(chatMode||i.isVisible(WindowInsets.Type.ime())?View.GONE:View.VISIBLE);return i.consumeSystemWindowInsets();});}
 void home(){
  saveDraft();drawer=false;chatMode=false;experimentalHome=getPreferences(0).getBoolean("conversation-home",true);if(!experimentalHome)chatMode=false;seenConversation=-2;base();page.setFocusableInTouchMode(true);
  LinearLayout header=new LinearLayout(this);header.setGravity(Gravity.CENTER_VERTICAL);header.setBaselineAligned(false);TextView brand=text(experimentalHome?"":"✦ Pixel",28,ink);brand.setOnLongClickListener(v->{homeOptions(brand);return true;});brand.setTypeface(null,Typeface.BOLD);guardianName=brand;header.addView(brand,new LinearLayout.LayoutParams(0,dp(44),1));guardianBack=button("Back to Pixel",()->command("guardian_back"));guardianBack.setTextSize(12);guardianBack.setVisibility(View.GONE);header.addView(guardianBack,new LinearLayout.LayoutParams(-2,dp(44)));brand.setGravity(Gravity.CENTER_VERTICAL);route=text("Microphone off",12,ink);route.setPadding(dp(12),dp(9),dp(12),dp(9));route.setBackground(raised(0xeafffff6,PixelAppearance.color(0xffd5e7f2),18));route.setVisibility(View.GONE);header.addView(route);if(experimentalHome){Button options=button("···",()->{});options.setTextSize(24);options.setBackgroundColor(Color.TRANSPARENT);options.setContentDescription("Home options");options.setOnClickListener(v->homeOptions(options));header.addView(options,new LinearLayout.LayoutParams(dp(44),dp(44)));}page.addView(header);
  taskScroll=new ScrollView(this);taskScroll.setFillViewport(false);taskScroll.setClipToPadding(false);taskScroll.setPadding(0,dp(10),0,dp(14));taskScroll.setVerticalScrollBarEnabled(false);LinearLayout topContent=new LinearLayout(this);topContent.setOrientation(1);taskScroll.addView(topContent,new ScrollView.LayoutParams(-1,-2));page.addView(taskScroll,new LinearLayout.LayoutParams(-1,0,1));
  taskCard=new TaskStatusCard(this);topContent.addView(taskCard,new LinearLayout.LayoutParams(-1,-2));
  visualSlot=new FrameLayout(this);visualSlot.setVisibility(View.GONE);LinearLayout.LayoutParams visualParams=new LinearLayout.LayoutParams(-1,-2);visualParams.topMargin=dp(12);topContent.addView(visualSlot,visualParams);seenVisual=-1;
  voiceCenter=new LinearLayout(this);voiceCenter.setOrientation(1);voiceCenter.setGravity(Gravity.TOP|Gravity.CENTER_HORIZONTAL);voiceCenter.setPadding(0,dp(8),0,0);page.addView(voiceCenter,new LinearLayout.LayoutParams(-1,-2));taskModel=text("Model connecting…",13,secondary);taskModel.setGravity(Gravity.CENTER);taskModel.setMaxLines(1);taskModel.setEllipsize(android.text.TextUtils.TruncateAt.END);LinearLayout.LayoutParams modelParams=new LinearLayout.LayoutParams(-1,dp(28));modelParams.bottomMargin=dp(8);voiceCenter.addView(taskModel,modelParams);micButton=new MicControl();micButton.setOnClickListener(v->toggleVoice());voiceCenter.addView(micButton,new LinearLayout.LayoutParams(dp(188),dp(188)));
  state=text("Tap to talk",18,secondary);state.setGravity(Gravity.CENTER);state.setTypeface(chatFont(800));state.setAccessibilityHeading(true);LinearLayout.LayoutParams titleParams=new LinearLayout.LayoutParams(-1,dp(28));titleParams.topMargin=dp(12);state.setVisibility(View.VISIBLE);voiceCenter.addView(state,titleParams);hint=text("Microphone off",15,secondary);hint.setGravity(Gravity.CENTER);hint.setMaxLines(2);LinearLayout.LayoutParams hintParams=new LinearLayout.LayoutParams(-1,dp(40));hintParams.topMargin=dp(4);hint.setVisibility(View.INVISIBLE);voiceCenter.addView(hint,hintParams);
  chatScroll=new ScrollView(this);chatScroll.setOnTouchListener((v,event)->{if(event.getActionMasked()==MotionEvent.ACTION_DOWN)userScrolling=true;return false;});chatScroll.setOnScrollChangeListener((v,x,y,oldX,oldY)->{if(userScrolling)stickToBottom=chatMessages.getHeight()-chatScroll.getHeight()-y<dp(80);});chatScroll.addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or,ob)->scrollChatToEnd());chatScroll.setFillViewport(true);chatScroll.setClipToPadding(false);chatScroll.setPadding(0,0,0,dp(12));chatMessages=new LinearLayout(this);chatMessages.addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or,ob)->scrollChatToEnd());chatMessages.setOrientation(1);chatScroll.addView(chatMessages,new ScrollView.LayoutParams(-1,-2));page.addView(chatScroll,new LinearLayout.LayoutParams(-1,0,1));chatScroll.setVisibility(experimentalHome&&chatMode?View.VISIBLE:View.GONE);
  breathingRoom=taskScroll;breathingRoom.setVisibility(experimentalHome&&chatMode?View.GONE:View.VISIBLE);if(experimentalHome&&chatMode)voiceCenter.setVisibility(View.GONE);
  endSlot=new FrameLayout(this);end=button("×",()->command("end"));end.setTextSize(36);end.setTypeface(chatFont(900));end.setIncludeFontPadding(false);end.setPadding(0,0,0,dp(5));end.setTextColor(0xff593447);android.graphics.drawable.StateListDrawable endStates=new android.graphics.drawable.StateListDrawable();endStates.addState(new int[]{android.R.attr.state_pressed},chatSurface(0xffffa18d,0xffad6458,32));endStates.addState(new int[]{},chatSurface(0xffffb5a3,0xffad6458,32));end.setBackground(endStates);end.setOnTouchListener((v,event)->{if(event.getActionMasked()==MotionEvent.ACTION_DOWN&&v.isEnabled())v.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);return false;});end.setContentDescription("End voice session");endSlot.addView(end,new FrameLayout.LayoutParams(dp(62),dp(62),Gravity.CENTER_HORIZONTAL));page.addView(endSlot,new LinearLayout.LayoutParams(-1,dp(76)));
  task=text("",14,secondary);task.setMaxLines(4);task.setPadding(dp(16),dp(12),dp(16),dp(12));task.setBackground(raised(PixelAppearance.color(0xeafff7e5),PixelAppearance.color(0xffd5e7f2),18));LinearLayout.LayoutParams taskParams=new LinearLayout.LayoutParams(-1,-2);taskParams.bottomMargin=dp(14);topContent.addView(task,taskParams);
  LinearLayout entry=new LinearLayout(this);entry.setGravity(Gravity.CENTER_VERTICAL);entry.setPadding(dp(14),0,dp(5),0);entry.setBackground(raised(PixelAppearance.color(0xfffff7e5),PixelAppearance.color(0xffb3d0e4),24));
  composer=new EditText(this);composer.setTextSize(experimentalHome?22:16);if(experimentalHome){composer.setTypeface(chatFont(800));composer.setIncludeFontPadding(false);composer.setLetterSpacing(.005f);}composer.setTextColor(ink);composer.setHintTextColor(secondary);composer.setHint("Message…");composer.setContentDescription("Message Pixel");composer.setSingleLine(true);composer.setOnFocusChangeListener((v,focused)->{if(focused&&experimentalHome)openChat();});composer.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);composer.setImeOptions(android.view.inputmethod.EditorInfo.IME_ACTION_SEND);composer.setFilters(new android.text.InputFilter[]{new android.text.InputFilter.LengthFilter(12000)});composer.setBackgroundColor(Color.TRANSPARENT);composer.setPadding(0,0,dp(8),0);composer.setText(getPreferences(0).getString("typed-draft",""));composer.setOnEditorActionListener((v,action,event)->{if(action==android.view.inputmethod.EditorInfo.IME_ACTION_SEND){sendTyped();return true;}return false;});entry.addView(composer,new LinearLayout.LayoutParams(0,dp(experimentalHome?68:54),1));
  send=button("↑",()->sendTyped());send.setTextSize(24);send.setTextColor(Color.WHITE);send.setBackground(raised(green,PixelAppearance.color(0xff3f567a),18));send.setPadding(0,0,0,0);send.setContentDescription("Send message");if(experimentalHome){entry.setBackground(chatSurface(PixelAppearance.color(0xfffff7e5),PixelAppearance.dark()?0xff101a2b:0xff789ab5,25));entry.setPadding(dp(20),0,dp(7),dp(5));send.setTypeface(chatFont(900));send.setTextSize(30);send.setTextColor(0xff29385d);send.setBackground(chatSurface(0xffffd66b,0xffb9872f,20));}entry.addView(send,new LinearLayout.LayoutParams(dp(experimentalHome?54:44),dp(experimentalHome?54:44)));page.addView(entry,new LinearLayout.LayoutParams(-1,dp(experimentalHome?70:54)));entry.setVisibility(View.GONE);
  LinearLayout footer=new LinearLayout(this);footer.setGravity(Gravity.CENTER_VERTICAL);footer.setBaselineAligned(false);connection=text("Voice off",12,secondary);connection.setGravity(Gravity.CENTER_VERTICAL);connection.setIncludeFontPadding(false);footer.addView(connection,new LinearLayout.LayoutParams(0,dp(54),1));Button apps=button("Apps  ▦",()->apps());apps.setTextSize(14);apps.setGravity(Gravity.CENTER);apps.setIncludeFontPadding(false);apps.setPadding(0,0,0,0);apps.setTextColor(secondary);apps.setBackground(raised(PixelAppearance.color(0xcffff7e5),PixelAppearance.color(0xffb3d0e4),18));footer.addView(apps,new LinearLayout.LayoutParams(dp(100),dp(42)));footer.setVisibility(View.GONE);page.addView(footer);page.requestFocus();attachLauncherPages();refresh();
 }

 Typeface chatFont(int weight){if(roundedFont==null)roundedFont=Typeface.createFromAsset(getAssets(),"fonts/nunito.ttf");return Typeface.create(roundedFont,weight,false);}
 android.graphics.drawable.Drawable chatSurface(int face,int shadow,int radius){
  float d=getResources().getDisplayMetrics().density;
  GradientDrawable back=new GradientDrawable();back.setColor(shadow);back.setCornerRadius(dp(radius));
  GradientDrawable front=new GradientDrawable();front.setColor(face);front.setCornerRadius(dp(radius));front.setStroke(dp(2),shadow);
  android.graphics.drawable.LayerDrawable layers=new android.graphics.drawable.LayerDrawable(new android.graphics.drawable.Drawable[]{back,front});layers.setLayerInset(0,0,dp(5),0,0);layers.setLayerInset(1,0,0,0,dp(5));return layers;
 }
 void guardianPicker(){EditText name=new EditText(this);name.setHint("Member name");new AlertDialog.Builder(this).setTitle("Talk to a Guardian").setView(name).setNegativeButton("Cancel",null).setPositiveButton("Connect",(d,w)->{VoiceService service=VoiceService.instance;if(service!=null)service.connectGuardian(name.getText().toString());}).show();}
 void homeOptions(View anchor){PopupMenu menu=new PopupMenu(this,anchor);menu.getMenu().add("Server connection").setOnMenuItemClickListener(i->{startActivity(new Intent(this,ServerConnectionActivity.class));return true;});menu.getMenu().add("Conversation mode").setCheckable(true).setChecked(getSharedPreferences("conversation-mode",0).getBoolean("active",false)).setOnMenuItemClickListener(i->{command("toggle_conversation_mode");return true;});menu.getMenu().add("Conversation notes").setOnMenuItemClickListener(i->{startActivity(new Intent(this,ConversationModeActivity.class));return true;});menu.getMenu().add("Pair with Mac").setOnMenuItemClickListener(i->{startActivity(new Intent(this,PairingActivity.class));return true;});menu.getMenu().add("Location access").setOnMenuItemClickListener(i->{if(PhoneEnvironment.permitted(this))startActivity(new Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,android.net.Uri.parse("package:"+getPackageName())));else requestLocationContext();return true;});menu.getMenu().add("General Decisions routing").setCheckable(true).setChecked(getSharedPreferences("decisions-routing",0).getBoolean("enabled",false)).setOnMenuItemClickListener(i->{command("toggle_decisions_routing");return true;});menu.getMenu().add("Live Mac screen").setCheckable(true).setChecked(macScreenEnabled()).setOnMenuItemClickListener(i->{getPreferences(0).edit().putBoolean("mac-screen-enabled",!macScreenEnabled()).apply();home();return true;});menu.getMenu().add("Keep screen on").setCheckable(true).setChecked(keepScreenOnEnabled()).setOnMenuItemClickListener(i->{setKeepScreenOnEnabled(!keepScreenOnEnabled());return true;});menu.getMenu().add("App control while locked").setCheckable(true).setChecked(LockedControl.enabled(this)).setOnMenuItemClickListener(i->{toggleLockedControl();return true;});if(experimentalHome){menu.getMenu().add("Classic home").setOnMenuItemClickListener(i->{getPreferences(0).edit().putBoolean("conversation-home",false).apply();chatMode=false;home();return true;});}else{menu.getMenu().add("Try conversation home").setOnMenuItemClickListener(i->{getPreferences(0).edit().putBoolean("conversation-home",true).apply();home();return true;});}if(VISUAL_CARDS)menu.getMenu().add("Visual cards").setCheckable(true).setChecked(VisualStore.enabled(this)).setOnMenuItemClickListener(i->{VisualStore.setEnabled(this,!VisualStore.enabled(this));syncVisual();return true;});menu.getMenu().add("Reject changed screens").setCheckable(true).setChecked(getSharedPreferences("screen-change-guard",0).getBoolean("enabled",true)).setOnMenuItemClickListener(i->{android.content.SharedPreferences prefs=getSharedPreferences("screen-change-guard",0);prefs.edit().putBoolean("enabled",!prefs.getBoolean("enabled",true)).apply();return true;});menu.getMenu().add("Stable-field typing").setCheckable(true).setChecked(getSharedPreferences("text-input-guard",0).getBoolean("enabled",false)).setOnMenuItemClickListener(i->{android.content.SharedPreferences prefs=getSharedPreferences("text-input-guard",0);prefs.edit().putBoolean("enabled",!prefs.getBoolean("enabled",false)).apply();return true;});menu.getMenu().add("Voice boost (+6 dB)").setCheckable(true).setChecked(getSharedPreferences("voice-output-boost",0).getBoolean("enabled",false)).setOnMenuItemClickListener(i->{VoiceService s=VoiceService.instance;if(s!=null)s.setVoiceBoostEnabled(!s.voiceBoostEnabled());return true;});VoiceService current=VoiceService.instance;AudioDeviceInfo glasses=current==null?null:current.bluetoothDevice();if(glasses!=null){String key=glasses.getProductName().toString()+"-double-tap-mute";menu.getMenu().add("Double tap toggles mute").setCheckable(true).setChecked(getSharedPreferences("glasses-controls",0).getBoolean(key,false)).setOnMenuItemClickListener(i->{android.content.SharedPreferences prefs=getSharedPreferences("glasses-controls",0);prefs.edit().putBoolean(key,!prefs.getBoolean(key,false)).apply();return true;});}menu.getMenu().add("Set up connected glasses").setOnMenuItemClickListener(i->{GlassesSetup.begin(this);return true;});menu.getMenu().add("Talk to a Guardian").setOnMenuItemClickListener(i->{guardianPicker();return true;});menu.getMenu().add("Your apps").setOnMenuItemClickListener(i->{apps();return true;});menu.getMenu().add("Result card style").setOnMenuItemClickListener(i->{new AlertDialog.Builder(this).setTitle("Result card style").setSingleChoiceItems(new String[]{"Agent HTML (trial)","Structured native","Plain text"},getSharedPreferences("task-card",0).getString("mode","native").equals("html")?0:getSharedPreferences("task-card",0).getBoolean("rich",true)?1:2,(dialog,which)->{VoiceService s=VoiceService.instance;if(s!=null)s.setResultCardMode(new String[]{"html","native","text"}[which]);dialog.dismiss();}).show();return true;});menu.show();}
 void scrollChatToEnd(){if(chatMode&&stickToBottom&&chatScroll!=null&&chatMessages!=null)chatScroll.post(()->{if(chatMode&&stickToBottom)chatScroll.scrollTo(0,chatMessages.getHeight());});}
 void openChat(){if(!experimentalHome)return;chatMode=true;forceChatBottom=true;stickToBottom=true;userScrolling=false;voiceCenter.setVisibility(View.GONE);breathingRoom.setVisibility(View.GONE);chatScroll.setVisibility(View.VISIBLE);if(visualSlot!=null)visualSlot.setVisibility(View.GONE);scrollChatToEnd();}
 void renderConversation(String raw){
  if(chatMessages==null)return;
  try{org.json.JSONArray messages=new org.json.JSONObject(raw).optJSONArray("messages");if(messages==null)return;
   boolean follow=forceChatBottom||stickToBottom;
   forceChatBottom=false;int previousY=chatScroll.getScrollY();chatMessages.removeAllViews();
   for(int i=Math.max(0,messages.length()-40);i<messages.length();i++){org.json.JSONObject m=messages.getJSONObject(i);String content=m.optString("text").trim();if(content.isEmpty())continue;boolean own="user".equals(m.optString("role"));
    LinearLayout row=new LinearLayout(this);row.setGravity(own?Gravity.RIGHT:Gravity.LEFT);row.setPadding(own?dp(32):0,dp(8),own?0:dp(18),dp(12));TextView message=text(content,22,ink);message.setTypeface(chatFont(800));message.setIncludeFontPadding(false);message.setLetterSpacing(.005f);message.setLineSpacing(dp(5),1);message.setTextIsSelectable(true);message.setPadding(dp(20),dp(17),dp(20),dp(22));message.setBackground(chatSurface((own?(PixelAppearance.dark()?0xff375e8b:0xffc7e7ff):PixelAppearance.color(0xfffff7e5)),PixelAppearance.dark()?0xff101a2b:0xff789ab5,24));row.addView(message,new LinearLayout.LayoutParams(-2,-2));chatMessages.addView(row,new LinearLayout.LayoutParams(-1,-2));
   }
   if(follow)scrollChatToEnd();else chatScroll.post(()->chatScroll.scrollTo(0,previousY));
  }catch(Exception ignored){}
 }

 // ---- Home visual card: declarative data from VisualStore, drawn with the home theme. Shows supplied values only. ----
 int visualCream(){return PixelAppearance.color(0xfffff7e5);}
 int visualEdge(){return PixelAppearance.dark()?0xff101a2b:0xff789ab5;}
 TextView visualText(String s,int size,int color,int weight){TextView t=text(s,size,color);t.setTypeface(chatFont(weight));t.setIncludeFontPadding(false);return t;}
 String visualJoin(String... parts){StringBuilder b=new StringBuilder();for(String part:parts)if(part!=null&&!part.isEmpty()){if(b.length()>0)b.append(" · ");b.append(part);}return b.toString();}
 /** Supplied numbers are shown as supplied; no rounding, no unit conversion. */
 String visualNumber(double value){return java.math.BigDecimal.valueOf(value).stripTrailingZeros().toPlainString();}
 String visualValue(JSONObject nutrient){if(nutrient.isNull("value"))return "—";String unit=nutrient.optString("unit");return visualNumber(nutrient.optDouble("value"))+(unit.isEmpty()?"":" "+unit);}

 /** Native, schematic inland-to-coast cross-section for declarative wind cards. */
 final class WindDiagramView extends View {
  final boolean compact;final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
  WindDiagramView(Context context,boolean compact){super(context);this.compact=compact;setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);}
  void color(Canvas c,int color){p.setColor(color);p.setStyle(Paint.Style.FILL);}
  void label(Canvas c,String value,float x,float y,float size,int color,Paint.Align align){
   color(c,color);p.setTypeface(Typeface.create("sans-serif-rounded",Typeface.BOLD));p.setTextSize(size);p.setTextAlign(align);c.drawText(value,x,y,p);
  }
  void arrow(Canvas c,float x1,float y1,float x2,float y2,int color,float width){
   color(c,color);p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(width);p.setStrokeCap(Paint.Cap.ROUND);p.setStrokeJoin(Paint.Join.ROUND);c.drawLine(x1,y1,x2,y2,p);p.setStyle(Paint.Style.FILL);
   float angle=(float)Math.atan2(y2-y1,x2-x1),len=8;Path head=new Path();head.moveTo(x2,y2);head.lineTo(x2-len*(float)Math.cos(angle-.55f),y2-len*(float)Math.sin(angle-.55f));head.lineTo(x2-len*(float)Math.cos(angle+.55f),y2-len*(float)Math.sin(angle+.55f));head.close();c.drawPath(head,p);
  }
  @Override protected void onDraw(Canvas canvas){
   super.onDraw(canvas);float density=getResources().getDisplayMetrics().density,w=getWidth()/density,h=getHeight()/density;canvas.save();canvas.scale(density,density);
   int sky=PixelAppearance.color(0xffd5e7f2),ink=PixelAppearance.color(0xff29385d),muted=PixelAppearance.color(0xff576f90),coral=PixelAppearance.color(0xffa9433e),ground=PixelAppearance.color(0xffd7b978);
   color(canvas,sky);canvas.drawRoundRect(0,0,w,h,16,16,p);
   Path terrain=new Path();float base=h*(compact?.78f:.73f);terrain.moveTo(0,base);terrain.lineTo(w*.30f,base);terrain.lineTo(w*.44f,h*(compact?.25f:.30f));terrain.lineTo(w*.52f,h*(compact?.35f:.37f));terrain.lineTo(w*.67f,h*(compact?.49f:.48f));terrain.lineTo(w,base);terrain.lineTo(w,h);terrain.lineTo(0,h);terrain.close();color(canvas,ground);canvas.drawPath(terrain,p);
   color(canvas,PixelAppearance.color(0xffb68d58));p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(1.2f);canvas.drawLine(0,base,w,base,p);p.setStyle(Paint.Style.FILL);
   if(compact){
    color(canvas,PixelAppearance.color(0xfffff7e5));canvas.drawCircle(w*.14f,h*.34f,8,p);label(canvas,"H",w*.14f,h*.34f+3.5f,8,ink,Paint.Align.CENTER);
    arrow(canvas,w*.28f,h*.25f,w*.47f,h*.20f,coral,2.6f);arrow(canvas,w*.53f,h*.23f,w*.83f,h*.58f,coral,2.6f);
   }else{
    label(canvas,"GREAT BASIN",w*.15f,19,11,muted,Paint.Align.CENTER);label(canvas,"MOUNTAINS",w*.50f,19,11,muted,Paint.Align.CENTER);label(canvas,"COAST",w*.87f,19,11,muted,Paint.Align.CENTER);
    color(canvas,PixelAppearance.color(0xfffff7e5));canvas.drawCircle(w*.15f,51,16,p);label(canvas,"H",w*.15f,56,16,ink,Paint.Align.CENTER);
    arrow(canvas,w*.25f,48,w*.43f,43,coral,3.5f);arrow(canvas,w*.48f,43,w*.68f,67,coral,3.5f);arrow(canvas,w*.70f,71,w*.88f,base-7,coral,3.5f);
    label(canvas,"OFFSHORE FLOW",w*.51f,h-11,11,ink,Paint.Align.CENTER);
   }
   canvas.restore();
  }
 }

 /** Called from the refresh tick. Does nothing unless the store revision moved. */
 void syncVisual(){
  if(visualSlot==null)return;
  if(macScreenEnabled()){showMacCard();return;}
  if(!VISUAL_CARDS)return;
  try{
   long revision=VisualStore.revision(this);if(revision==seenVisual)return;seenVisual=revision;
   String before=visualData==null?"":visualData.toString();visualData=VisualStore.visible(this);visualSlot.removeAllViews();
   if(visualData==null){visualSlot.setVisibility(View.GONE);closeVisual();return;}
   visualSlot.addView(visualCard(visualData),new FrameLayout.LayoutParams(-1,dp(78)));visualSlot.setVisibility(chatMode?View.GONE:View.VISIBLE);
   if(visualDetail!=null&&!before.equals(visualData.toString()))openVisual();
  }catch(Exception e){visualData=null;visualSlot.removeAllViews();visualSlot.setVisibility(View.GONE);closeVisual();}
 }
 View visualCard(JSONObject visual){
  LinearLayout card=new LinearLayout(this);card.setGravity(Gravity.CENTER_VERTICAL);card.setPadding(dp(20),0,dp(8),dp(5));card.setBackground(chatSurface(visualCream(),visualEdge(),24));
  card.setClickable(true);card.setFocusable(true);card.setOnClickListener(v->openVisual());
  LinearLayout words=new LinearLayout(this);words.setOrientation(1);
  TextView title=visualText(visual.optString("title"),19,ink,900);title.setSingleLine(true);title.setEllipsize(android.text.TextUtils.TruncateAt.END);words.addView(title);
  String sub=visualJoin(visual.optString("subtitle"),visual.optString("source"));
  if(!sub.isEmpty()){TextView subtitle=visualText(sub,14,secondary,800);subtitle.setSingleLine(true);subtitle.setEllipsize(android.text.TextUtils.TruncateAt.END);LinearLayout.LayoutParams subParams=new LinearLayout.LayoutParams(-1,-2);subParams.topMargin=dp(5);words.addView(subtitle,subParams);}
  card.addView(words,new LinearLayout.LayoutParams(0,-2,1));
  if(visual.optString("kind","meals").equals("wind")){
   WindDiagramView diagram=new WindDiagramView(this,true);LinearLayout.LayoutParams diagramParams=new LinearLayout.LayoutParams(dp(92),dp(54));diagramParams.leftMargin=dp(8);diagramParams.rightMargin=dp(4);card.addView(diagram,diagramParams);
   JSONObject wind=visual.optJSONObject("wind");String detail=wind==null?"":visualJoin(wind.optString("origin"),wind.optString("barrier"),wind.optString("destination"),wind.optString("effect"),wind.optString("peak"));
   card.setContentDescription(visualJoin(visual.optString("title"),sub,detail)+". Open");
  }else card.setContentDescription(visualJoin(visual.optString("title"),sub)+". Open");
  if(VisualStore.pinned(this)){TextView pin=visualText("★",20,secondary,900);pin.setGravity(Gravity.CENTER);pin.setContentDescription("Pinned");card.addView(pin,new LinearLayout.LayoutParams(dp(48),dp(48)));}
  else{Button dismiss=button("×",()->{VisualStore.dismiss(this);syncVisual();});dismiss.setTextSize(26);dismiss.setTypeface(chatFont(900));dismiss.setIncludeFontPadding(false);dismiss.setPadding(0,0,0,dp(3));dismiss.setTextColor(secondary);dismiss.setBackgroundColor(Color.TRANSPARENT);dismiss.setContentDescription("Dismiss card");card.addView(dismiss,new LinearLayout.LayoutParams(dp(48),dp(48)));}
  return card;
 }
 void closeVisual(){
  if(visualDetail==null)return;
  if(visualDetail.getParent() instanceof ViewGroup)((ViewGroup)visualDetail.getParent()).removeView(visualDetail);
  visualDetail=null;if(page!=null)page.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_AUTO);
 }
 /** Full-screen detail over home. While it is open the Apps swipe is not watched, so its list scrolls freely. */
 void openVisual(){
  closeVisual();if(visualData==null||drawer)return;
  if(visualData.optString("kind","meals").equals("wind")){openWindVisual();return;}
  JSONArray days=visualData.optJSONArray("days");if(days==null||days.length()==0)return;
  if(visualDay>=days.length())visualDay=0;
  FrameLayout overlay=new FrameLayout(this);overlay.setBackground(new PlayfulTheme.World());overlay.setClickable(true);overlay.setPadding(page.getPaddingLeft(),page.getPaddingTop(),page.getPaddingRight(),page.getPaddingBottom());
  LinearLayout column=new LinearLayout(this);column.setOrientation(1);overlay.addView(column,new FrameLayout.LayoutParams(-1,-1));
  LinearLayout top=new LinearLayout(this);top.setGravity(Gravity.CENTER_VERTICAL);
  TextView title=visualText(visualData.optString("title"),26,ink,900);title.setMaxLines(2);title.setEllipsize(android.text.TextUtils.TruncateAt.END);title.setAccessibilityHeading(true);top.addView(title,new LinearLayout.LayoutParams(0,-2,1));
  Button close=button("×",this::closeVisual);close.setTextSize(28);close.setTypeface(chatFont(900));close.setIncludeFontPadding(false);close.setPadding(0,0,0,dp(6));close.setBackground(chatSurface(visualCream(),visualEdge(),26));close.setContentDescription("Close");
  LinearLayout.LayoutParams closeParams=new LinearLayout.LayoutParams(dp(54),dp(54));closeParams.leftMargin=dp(12);top.addView(close,closeParams);column.addView(top,new LinearLayout.LayoutParams(-1,dp(60)));
  String meta=visualJoin(visualData.optString("subtitle"),visualData.optString("source"));
  if(!meta.isEmpty())column.addView(visualText(meta,14,secondary,800));
  HorizontalScrollView strip=new HorizontalScrollView(this);strip.setHorizontalScrollBarEnabled(false);strip.setClipToPadding(false);
  visualDays=new LinearLayout(this);strip.addView(visualDays);
  for(int i=0;i<days.length();i++){
   JSONObject day=days.optJSONObject(i);String name=day==null?"":day.optString("label");if(name.isEmpty()&&day!=null)name=day.optString("date");final int index=i;
   Button chip=button(name,()->{visualDay=index;renderVisualDay();});chip.setTypeface(chatFont(900));chip.setTextSize(16);chip.setIncludeFontPadding(false);chip.setPadding(dp(20),0,dp(20),dp(5));
   LinearLayout.LayoutParams chipParams=new LinearLayout.LayoutParams(-2,dp(52));chipParams.rightMargin=dp(10);visualDays.addView(chip,chipParams);
  }
  LinearLayout.LayoutParams stripParams=new LinearLayout.LayoutParams(-1,-2);stripParams.topMargin=dp(14);column.addView(strip,stripParams);
  ScrollView scroll=new ScrollView(this);scroll.setClipToPadding(false);scroll.setPadding(0,dp(4),0,dp(12));scroll.setVerticalScrollBarEnabled(false);
  visualBody=new LinearLayout(this);visualBody.setOrientation(1);scroll.addView(visualBody,new ScrollView.LayoutParams(-1,-2));
  LinearLayout.LayoutParams scrollParams=new LinearLayout.LayoutParams(-1,0,1);scrollParams.topMargin=dp(10);column.addView(scroll,scrollParams);
  LinearLayout actions=new LinearLayout(this);actions.setGravity(Gravity.CENTER_VERTICAL);actions.setBaselineAligned(false);
  visualPin=button("",()->{VisualStore.setPinned(this,!VisualStore.pinned(this));styleVisualPin();syncVisual();});visualPin.setTypeface(chatFont(900));visualPin.setTextSize(17);visualPin.setIncludeFontPadding(false);visualPin.setPadding(0,0,0,dp(5));
  actions.addView(visualPin,new LinearLayout.LayoutParams(0,dp(58),1));
  visualDismiss=button("Dismiss",()->{VisualStore.dismiss(this);syncVisual();});visualDismiss.setTypeface(chatFont(900));visualDismiss.setTextSize(17);visualDismiss.setIncludeFontPadding(false);visualDismiss.setPadding(0,0,0,dp(5));visualDismiss.setTextColor(0xff593447);visualDismiss.setBackground(chatSurface(0xffffb5a3,0xffad6458,24));
  LinearLayout.LayoutParams dismissParams=new LinearLayout.LayoutParams(0,dp(58),1);dismissParams.leftMargin=dp(12);actions.addView(visualDismiss,dismissParams);
  LinearLayout.LayoutParams actionParams=new LinearLayout.LayoutParams(-1,-2);actionParams.topMargin=dp(8);column.addView(actions,actionParams);
  addContentView(overlay,new ViewGroup.LayoutParams(-1,-1));visualDetail=overlay;page.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
  styleVisualPin();renderVisualDay();
 }
 void openWindVisual(){
  JSONObject wind=visualData.optJSONObject("wind");if(wind==null)return;
  FrameLayout overlay=new FrameLayout(this);overlay.setBackground(new PlayfulTheme.World());overlay.setClickable(true);overlay.setPadding(page.getPaddingLeft(),page.getPaddingTop(),page.getPaddingRight(),page.getPaddingBottom());
  LinearLayout column=new LinearLayout(this);column.setOrientation(1);overlay.addView(column,new FrameLayout.LayoutParams(-1,-1));
  LinearLayout top=new LinearLayout(this);top.setGravity(Gravity.CENTER_VERTICAL);
  TextView title=visualText(visualData.optString("title"),26,ink,900);title.setMaxLines(2);title.setEllipsize(android.text.TextUtils.TruncateAt.END);title.setAccessibilityHeading(true);top.addView(title,new LinearLayout.LayoutParams(0,-2,1));
  Button close=button("×",this::closeVisual);close.setTextSize(28);close.setTypeface(chatFont(900));close.setIncludeFontPadding(false);close.setPadding(0,0,0,dp(6));close.setBackground(chatSurface(visualCream(),visualEdge(),26));close.setContentDescription("Close");
  LinearLayout.LayoutParams closeParams=new LinearLayout.LayoutParams(dp(54),dp(54));closeParams.leftMargin=dp(12);top.addView(close,closeParams);column.addView(top,new LinearLayout.LayoutParams(-1,dp(60)));
  String meta=visualJoin(visualData.optString("subtitle"),visualData.optString("source"));if(!meta.isEmpty())column.addView(visualText(meta,14,secondary,800));
  ScrollView scroll=new ScrollView(this);scroll.setClipToPadding(false);scroll.setPadding(0,dp(12),0,dp(14));scroll.setVerticalScrollBarEnabled(false);
  LinearLayout body=new LinearLayout(this);body.setOrientation(1);scroll.addView(body,new ScrollView.LayoutParams(-1,-2));
  WindDiagramView diagram=new WindDiagramView(this,false);diagram.setContentDescription(visualJoin("Schematic airflow",wind.optString("origin"),wind.optString("barrier"),wind.optString("destination"),wind.optString("direction")));LinearLayout.LayoutParams diagramParams=new LinearLayout.LayoutParams(-1,dp(178));body.addView(diagram,diagramParams);
  visualWindFact(body,"Wind direction",wind.optString("direction"));
  visualWindFact(body,"The route",visualJoin(wind.optString("origin"),wind.optString("barrier"),wind.optString("destination")));
  visualWindFact(body,"Why it warms and dries",wind.optString("effect"));
  visualWindFact(body,"Timing",wind.optString("peak"));
  if(!wind.optString("caution").isEmpty())visualWindFact(body,"Regional heads-up",wind.optString("caution"));
  if(!visualData.optString("updatedAt").isEmpty())visualWindFact(body,"Forecast issued",visualData.optString("updatedAt"));
  LinearLayout.LayoutParams scrollParams=new LinearLayout.LayoutParams(-1,0,1);scrollParams.topMargin=dp(12);column.addView(scroll,scrollParams);
  LinearLayout actions=new LinearLayout(this);actions.setGravity(Gravity.CENTER_VERTICAL);actions.setBaselineAligned(false);
  visualPin=button("",()->{VisualStore.setPinned(this,!VisualStore.pinned(this));styleVisualPin();syncVisual();});visualPin.setTypeface(chatFont(900));visualPin.setTextSize(17);visualPin.setIncludeFontPadding(false);visualPin.setPadding(0,0,0,dp(5));actions.addView(visualPin,new LinearLayout.LayoutParams(0,dp(58),1));
  visualDismiss=button("Dismiss",()->{VisualStore.dismiss(this);syncVisual();});visualDismiss.setTypeface(chatFont(900));visualDismiss.setTextSize(17);visualDismiss.setIncludeFontPadding(false);visualDismiss.setPadding(0,0,0,dp(5));visualDismiss.setTextColor(0xff593447);visualDismiss.setBackground(chatSurface(0xffffb5a3,0xffad6458,24));
  LinearLayout.LayoutParams dismissParams=new LinearLayout.LayoutParams(0,dp(58),1);dismissParams.leftMargin=dp(12);actions.addView(visualDismiss,dismissParams);LinearLayout.LayoutParams actionParams=new LinearLayout.LayoutParams(-1,-2);actionParams.topMargin=dp(8);column.addView(actions,actionParams);
  addContentView(overlay,new ViewGroup.LayoutParams(-1,-1));visualDetail=overlay;page.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);styleVisualPin();
 }
 void visualWindFact(LinearLayout parent,String heading,String body){
  LinearLayout panel=new LinearLayout(this);panel.setOrientation(1);panel.setPadding(dp(18),dp(15),dp(18),dp(17));panel.setBackground(chatSurface(visualCream(),visualEdge(),22));
  TextView label=visualText(heading,15,secondary,900);panel.addView(label);TextView value=visualText(body,18,ink,800);value.setPadding(0,dp(5),0,0);value.setLineSpacing(dp(3),1);panel.addView(value);
  LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(-1,-2);params.topMargin=dp(10);parent.addView(panel,params);
 }
 /** A pinned card cannot be dismissed until it is unpinned. */
 void styleVisualPin(){
  boolean pinned=VisualStore.pinned(this);visualPin.setText(pinned?"★ Pinned":"Pin");visualPin.setTextColor(pinned?0xff29385d:ink);
  visualPin.setBackground(pinned?chatSurface(0xffffd66b,0xffb9872f,24):chatSurface(visualCream(),visualEdge(),24));visualPin.setContentDescription(pinned?"Unpin card":"Pin card");
  visualDismiss.setVisibility(pinned?View.GONE:View.VISIBLE);
 }
 void renderVisualDay(){
  try{
   for(int i=0;i<visualDays.getChildCount();i++){Button chip=(Button)visualDays.getChildAt(i);boolean on=i==visualDay;chip.setSelected(on);chip.setTextColor(on?0xff29385d:ink);chip.setBackground(on?chatSurface(0xffffd66b,0xffb9872f,24):chatSurface(visualCream(),visualEdge(),24));}
   visualBody.removeAllViews();JSONArray meals=visualData.getJSONArray("days").getJSONObject(visualDay).optJSONArray("meals");
   if(meals==null||meals.length()==0){TextView none=visualText("No meals",18,secondary,800);none.setPadding(dp(4),dp(20),0,0);visualBody.addView(none);return;}
   if(meals.length()>1)visualTotals(meals);
   for(int i=0;i<meals.length();i++){JSONObject meal=meals.getJSONObject(i);visualPanel(meal.optString("name"),meal.optString("time"),meal.optBoolean("estimated")?"Estimated":"",meal.optString("notes"),meal.optJSONArray("nutrients"));}
  }catch(Exception e){closeVisual();}
 }
 /** Adds supplied values only, per nutrient name and unit. Meals without a value are counted out and that is shown. */
 void visualTotals(JSONArray meals)throws JSONException{
  LinkedHashMap<String,JSONObject> totals=new LinkedHashMap<>();boolean estimated=false;
  for(int i=0;i<meals.length();i++){
   JSONObject meal=meals.getJSONObject(i);JSONArray nutrients=meal.optJSONArray("nutrients");if(nutrients==null)continue;boolean counted=false;
   for(int n=0;n<nutrients.length();n++){
    JSONObject nutrient=nutrients.getJSONObject(n);if(nutrient.isNull("value"))continue;
    String key=nutrient.optString("name").toLowerCase(Locale.ROOT)+"\n"+nutrient.optString("unit");JSONObject total=totals.get(key);
    if(total==null){total=new JSONObject().put("name",nutrient.optString("name")).put("unit",nutrient.optString("unit")).put("group",nutrient.optString("group")).put("sum",0d).put("meals",0).put("last",-1);totals.put(key,total);}
    total.put("sum",total.getDouble("sum")+nutrient.getDouble("value"));if(total.getInt("last")!=i)total.put("last",i).put("meals",total.getInt("meals")+1);counted=true;
   }
   if(counted&&meal.optBoolean("estimated"))estimated=true;
  }
  if(totals.isEmpty())return;JSONArray rows=new JSONArray();
  for(JSONObject total:totals.values()){
   total.put("value",new java.math.BigDecimal(total.getDouble("sum")).setScale(6,java.math.RoundingMode.HALF_UP).doubleValue());
   if(total.getInt("meals")<meals.length())total.put("note",total.getInt("meals")+" of "+meals.length()+" meals");rows.put(total);
  }
  visualPanel("Logged total","",estimated?"Includes estimates":"","",rows);
 }
 void visualPanel(String heading,String time,String flag,String notes,JSONArray nutrients)throws JSONException{
  LinearLayout panel=new LinearLayout(this);panel.setOrientation(1);panel.setPadding(dp(18),dp(18),dp(18),dp(23));panel.setBackground(chatSurface(visualCream(),visualEdge(),24));
  LinearLayout head=new LinearLayout(this);head.setGravity(Gravity.CENTER_VERTICAL);TextView name=visualText(heading,20,ink,900);name.setAccessibilityHeading(true);head.addView(name,new LinearLayout.LayoutParams(0,-2,1));
  if(!time.isEmpty()){TextView when=visualText(time,15,secondary,800);when.setPadding(dp(10),0,0,0);head.addView(when);}panel.addView(head);
  if(!flag.isEmpty()){TextView pill=visualText(flag,13,PixelAppearance.color(0xff923f38),900);pill.setPadding(dp(11),dp(6),dp(11),dp(6));pill.setBackground(shape(PixelAppearance.color(0xffffdace),12));LinearLayout.LayoutParams pillParams=new LinearLayout.LayoutParams(-2,-2);pillParams.topMargin=dp(9);panel.addView(pill,pillParams);}
  if(!notes.isEmpty()){TextView note=visualText(notes,14,secondary,800);note.setLineSpacing(dp(3),1);LinearLayout.LayoutParams noteParams=new LinearLayout.LayoutParams(-1,-2);noteParams.topMargin=dp(8);panel.addView(note,noteParams);}
  ArrayList<JSONObject> macros=new ArrayList<>(),others=new ArrayList<>();boolean allMicro=true;
  if(nutrients!=null)for(int i=0;i<nutrients.length();i++){JSONObject nutrient=nutrients.getJSONObject(i);String group=nutrient.optString("group");if(group.equals("macro"))macros.add(nutrient);else{others.add(nutrient);if(!group.equals("micro"))allMicro=false;}}
  LinearLayout row=null;
  for(int i=0;i<macros.size();i++){
   JSONObject nutrient=macros.get(i);
   if(i%2==0){row=new LinearLayout(this);row.setBaselineAligned(false);LinearLayout.LayoutParams rowParams=new LinearLayout.LayoutParams(-1,-2);rowParams.topMargin=dp(i==0?14:10);panel.addView(row,rowParams);}
   LinearLayout tile=new LinearLayout(this);tile.setOrientation(1);tile.setPadding(dp(14),dp(13),dp(14),dp(13));tile.setBackground(shape(PixelAppearance.color(0xffd5e7f2),16));
   tile.addView(visualText(visualValue(nutrient),20,ink,900));TextView label=visualText(nutrient.optString("name"),13,secondary,800);label.setPadding(0,dp(5),0,0);tile.addView(label);
   if(nutrient.has("note")){TextView note=visualText(nutrient.optString("note"),12,secondary,800);note.setPadding(0,dp(3),0,0);tile.addView(note);}
   tile.setContentDescription(visualJoin(nutrient.optString("name"),nutrient.isNull("value")?"unknown":visualValue(nutrient),nutrient.optString("note")));
   LinearLayout.LayoutParams tileParams=new LinearLayout.LayoutParams(0,-2,1);if(i%2==1)tileParams.leftMargin=dp(10);row.addView(tile,tileParams);
  }
  if(macros.size()%2==1){LinearLayout.LayoutParams gap=new LinearLayout.LayoutParams(0,1,1);gap.leftMargin=dp(10);row.addView(new View(this),gap);}
  if(!others.isEmpty()){
   LinearLayout list=new LinearLayout(this);list.setOrientation(1);list.setVisibility(View.GONE);String more=(allMicro?"Micros":"More")+" · "+others.size();
   Button toggle=button(more+"  ▾",()->{});toggle.setTypeface(chatFont(900));toggle.setTextSize(15);toggle.setIncludeFontPadding(false);toggle.setPadding(dp(18),0,dp(18),dp(5));toggle.setBackground(chatSurface(PixelAppearance.color(0xffd5e7f2),PixelAppearance.color(0xffb3d0e4),20));
   toggle.setOnClickListener(v->{boolean open=list.getVisibility()!=View.VISIBLE;list.setVisibility(open?View.VISIBLE:View.GONE);toggle.setText(more+(open?"  ▴":"  ▾"));});
   LinearLayout.LayoutParams toggleParams=new LinearLayout.LayoutParams(-2,dp(48));toggleParams.topMargin=dp(14);panel.addView(toggle,toggleParams);
   for(JSONObject nutrient:others){
    LinearLayout line=new LinearLayout(this);line.setGravity(Gravity.CENTER_VERTICAL);line.setPadding(dp(2),dp(11),dp(2),0);
    line.addView(visualText(visualJoin(nutrient.optString("name"),nutrient.optString("note")),15,secondary,800),new LinearLayout.LayoutParams(0,-2,1));line.addView(visualText(visualValue(nutrient),16,ink,900));
    line.setContentDescription(visualJoin(nutrient.optString("name"),nutrient.isNull("value")?"unknown":visualValue(nutrient),nutrient.optString("note")));list.addView(line);
   }
   panel.addView(list,new LinearLayout.LayoutParams(-1,-2));
  }
  LinearLayout.LayoutParams panelParams=new LinearLayout.LayoutParams(-1,-2);panelParams.topMargin=dp(12);visualBody.addView(panel,panelParams);
 }

 void apps(){if(launcherPager!=null)launcherPager.showPage(ourAppsPage,true);}
 boolean canSwipePages(MotionEvent event){
  if(screenDetail!=null||visualDetail!=null)return false;
  WindowInsets insets=getWindow().getDecorView().getRootWindowInsets();
  if(insets!=null&&insets.isVisible(WindowInsets.Type.ime()))return false;
  if(launcherPager!=null&&launcherPager.currentPage()==voicePage){
   if(touchInside(macPanel,event)||touchInside(composer,event))return false;
  }
  return true;
 }
 boolean touchInside(View view,MotionEvent event){
  if(view==null||!view.isShown())return false;
  int[] pos=new int[2];view.getLocationOnScreen(pos);
  return event.getRawX()>=pos[0]&&event.getRawX()<pos[0]+view.getWidth()
    &&event.getRawY()>=pos[1]&&event.getRawY()<pos[1]+view.getHeight();
 }
 void attachLauncherPages(){attachLauncherPages(false);}
 /** Our build certificate identifies apps we maintain, including forks with upstream package names.
  * Installer identity is unsuitable: official sideloaded apps also use our installer. */
 boolean ourBuild(String packageName){
  // These established suite apps use other signing keys. This is display grouping,
  // never a permission check; do not infer that arbitrary sideloaded/open-source apps are ours.
  return getPackageManager().checkSignatures(getPackageName(),packageName)==PackageManager.SIGNATURE_MATCH
    ||"dev.pixel.glassescamera".equals(packageName)||"dev.pixel.sky".equals(packageName)||"dev.pixel.weather.opus".equals(packageName)
    ||"com.bluebubbles.messaging".equals(packageName);
 }
 boolean preinstalled(ResolveInfo app){
  // An OS app stays preinstalled even when its current version is a store update.
  return (app.activityInfo.applicationInfo.flags&(ApplicationInfo.FLAG_SYSTEM|ApplicationInfo.FLAG_UPDATED_SYSTEM_APP))!=0;
 }
 boolean fromPlayStore(String packageName){
  try{
   String installer=Build.VERSION.SDK_INT>=30
     ?getPackageManager().getInstallSourceInfo(packageName).getInstallingPackageName()
     :getPackageManager().getInstallerPackageName(packageName);
   return "com.android.vending".equals(installer);
  }catch(PackageManager.NameNotFoundException|IllegalArgumentException|SecurityException unavailable){return false;}
 }
 void attachLauncherPages(boolean preservePage){
  // A rebuild that keeps the page keeps the legacy voice page too, even when the app pages before it change in number.
  int selected=preservePage&&launcherPager!=null?launcherPager.currentPage():-1;boolean onVoice=selected>=0&&selected==voicePage;
  if(clockWidget!=null)clockWidget.setActive(false);if(weatherWidget!=null)weatherWidget.setActive(false);if(galleryWidget!=null)galleryWidget.setActive(false);
  Intent query=new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
  ArrayList<ResolveInfo> ourApps=new ArrayList<>(),systemApps=new ArrayList<>(),storeApps=new ArrayList<>(),otherApps=new ArrayList<>();HashSet<String> seen=new HashSet<>(),suiteSeen=new HashSet<>();
  String cameraPackage=AppLabels.preferredCamera(getPackageManager());
  for(ResolveInfo app:getPackageManager().queryIntentActivities(query,0)){
   String key=app.activityInfo.packageName+"/"+app.activityInfo.name;
   if(!app.activityInfo.packageName.equals(getPackageName())&&seen.add(key)){
    String packageName=app.activityInfo.packageName;
    if(AppLabels.suiteIndex(packageName,cameraPackage)>=0){if(suiteSeen.add(packageName))ourApps.add(app);}
    else (ourBuild(packageName)?otherApps:preinstalled(app)?systemApps:fromPlayStore(packageName)?storeApps:otherApps).add(app);
   }
  }
  Comparator<ResolveInfo> byName=(a,b)->AppLabels.label(getPackageManager(),a.activityInfo.packageName,a.loadLabel(getPackageManager()).toString()).compareToIgnoreCase(AppLabels.label(getPackageManager(),b.activityInfo.packageName,b.loadLabel(getPackageManager()).toString()));
  for(ArrayList<ResolveInfo> group:Arrays.asList(systemApps,storeApps,otherApps))Collections.sort(group,byName);
  Collections.sort(ourApps,(a,b)->Integer.compare(AppLabels.suiteIndex(a.activityInfo.packageName,cameraPackage),AppLabels.suiteIndex(b.activityInfo.packageName,cameraPackage)));
  ArrayList<ArrayList<ResolveInfo>> pages=new ArrayList<>();ArrayList<String> titles=new ArrayList<>();
  // Home, then the app pages next to it under the fixed tray, then Widgets and the legacy voice page last (HomePages).
  titles.add("Home");pages.add(new ArrayList<>());
  // One continuous real inventory, our apps first, then preinstalled, Play Store and other installs, each in name order;
  // the page count follows from it and the inset viewport (HomeAppLayout), never a fixed count.
  ArrayList<ResolveInfo> launchable=new ArrayList<>();for(ArrayList<ResolveInfo> group:Arrays.asList(ourApps,systemApps,storeApps,otherApps))launchable.addAll(group);
  planAppPages();int appPages=HomeAppLayout.pages(launchable.size(),appPerPage);
  for(int i=0;i<appPages;i++){int[] range=HomeAppLayout.slice(launchable.size(),appPerPage,i);pages.add(new ArrayList<>(launchable.subList(range[0],range[1])));titles.add(HomeAppLayout.title(i,appPages));}
  titles.add("Widgets");pages.add(new ArrayList<>());
  titles.add("Voice home");pages.add(new ArrayList<>());
  int count=pages.size();homePage=HomePages.HOME;ourAppsPage=HomePages.FIRST_APPS;widgetsPage=HomePages.widgets(count);voicePage=HomePages.voice(count);
  if(selected<0||onVoice)selected=voicePage;
  // One wallpaper behind every page, drawn at the scroll position so it stays put while the pages slide.
  pageDots.clear();launcherPager=new LauncherPager(this);launcherPager.setBackground(new RoseHomeView.Wallpaper(getResources()));
  launcherPager.permission=this::canSwipePages;
  launcherPager.listener=index->{drawer=index!=voicePage;launcherPager.setContentDescription(titles.get(index));for(TextView dot:pageDots)dot.setAlpha(((Integer)dot.getTag())==index?1f:.45f);if(clockWidget!=null)clockWidget.setActive(screenHostActive&&index==widgetsPage);if(weatherWidget!=null)weatherWidget.setActive(screenHostActive&&index==widgetsPage);if(galleryWidget!=null)galleryWidget.setActive(screenHostActive&&index==widgetsPage);if(!drawer)refresh();};
  if(page.getParent() instanceof ViewGroup)((ViewGroup)page.getParent()).removeView(page);
  if(homePageDots!=null&&homePageDots.getParent() instanceof ViewGroup)((ViewGroup)homePageDots.getParent()).removeView(homePageDots);
  // The legacy voice page keeps its own artwork; the one row of page dots is fixed in the HomeFrame.
  homePageDots=null;LinearLayout fixedDots=makePageDots(titles);
  launcherPager.addView(roseHome=new RoseHomeView(this,RoseHomeView.dockApps(getPackageManager(),getPackageName(),launchable),homeColumns));
  for(int number=HomePages.FIRST_APPS;number<count;number++){
   if(number==voicePage){launcherPager.addView(page);continue;}
   if(number!=widgetsPage){launcherPager.addView(appPage(pages.get(number),titles.get(number),launchable.isEmpty()));continue;}
   // Widgets keeps its own artwork and heading; it is not an app page.
   LinearLayout shelfPage=new LinearLayout(this);shelfPage.setOrientation(1);shelfPage.setBackground(new PlayfulTheme.World());
   shelfPage.setPadding(dp(18),dp(20),dp(18),dp(12));
   shelfPage.setOnApplyWindowInsetsListener((v,i)->{v.setPadding(dp(18)+i.getSystemWindowInsetLeft(),dp(20)+i.getSystemWindowInsetTop(),dp(18)+i.getSystemWindowInsetRight(),dp(12)+i.getSystemWindowInsetBottom());return i;});
   TextView heading=text(titles.get(number),23,ink);heading.setTypeface(chatFont(900));heading.setAccessibilityHeading(true);heading.setPadding(dp(8),0,dp(8),dp(3));shelfPage.addView(heading);
   ScrollView scroll=new ScrollView(this);scroll.setFillViewport(true);scroll.setClipToPadding(false);scroll.setPadding(dp(8),dp(18),dp(8),dp(20));
   clockWidget=new ClockWidgetView(this,chatFont(900),()->{Intent launch=getPackageManager().getLaunchIntentForPackage("dev.pixel.clock");if(launch!=null)startActivity(launch);else Toast.makeText(this,"Clock is unavailable",Toast.LENGTH_SHORT).show();});
   weatherWidget=new WeatherWidgetView(this,chatFont(900));
   galleryWidget=new GalleryWidgetView(this,chatFont(900),this::requestPhotoAccess);
   LinearLayout widgets=new LinearLayout(this);widgets.setOrientation(LinearLayout.VERTICAL);widgets.addView(smallWidgetShelf(clockWidget,weatherWidget,galleryWidget),new LinearLayout.LayoutParams(-1,-2));
   TextView credit=text("Weather by Open-Meteo",11,secondary);credit.setTypeface(chatFont(800));credit.setPadding(dp(3),dp(12),0,dp(8));credit.setOnClickListener(v->startActivity(new Intent(Intent.ACTION_VIEW,android.net.Uri.parse("https://open-meteo.com/"))));widgets.addView(credit,new LinearLayout.LayoutParams(-2,-2));
   scroll.addView(widgets,new ScrollView.LayoutParams(-1,-2));shelfPage.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
   launcherPager.addView(shelfPage);
  }
  if(homeTray!=null)homeTray.release();homeTray=new HomeTray(this,this::openRoseConversation,this::openOverview,MainActivity::overviewAvailable);homeFrame=new HomeFrame(this,launcherPager,homeTray,fixedDots);
  setContentView(homeFrame);launcherPager.showPage(selected,false);homeFrame.requestApplyInsets();
  builtHomeStamp=homeStamp();
 }
 /** A small square uses one of two columns; wide screens do not inflate it. */
 ViewGroup smallWidgetShelf(View... widgets){
  ViewGroup shelf=new ViewGroup(this){
   int side,columns;
   @Override protected void onMeasure(int widthSpec,int heightSpec){
    int available=View.MeasureSpec.getSize(widthSpec);columns=available>=dp(3*190+2*16)?Math.min(3,Math.max(1,getChildCount())):available>=dp(320)?2:1;
    side=Math.min(dp(190),(available-dp(16)*(columns-1))/columns);
    for(int i=0;i<getChildCount();i++)getChildAt(i).measure(View.MeasureSpec.makeMeasureSpec(side,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(side,View.MeasureSpec.EXACTLY));
    int rows=(getChildCount()+columns-1)/columns;
    setMeasuredDimension(resolveSize(available,widthSpec),resolveSize(rows*side+(rows-1)*dp(16),heightSpec));
   }
   @Override protected void onLayout(boolean changed,int l,int t,int r,int b){for(int i=0;i<getChildCount();i++){int x=(i%columns)*(side+dp(16)),y=(i/columns)*(side+dp(16));getChildAt(i).layout(x,y,x+side,y+side);}}
  };
  for(View widget:widgets)shelf.addView(widget,new ViewGroup.LayoutParams(dp(190),dp(190)));return shelf;
 }
 /** Columns and apps per page from the inset viewport (HomeAppLayout): the real measured app area once a page has
  * reported it, else an estimate from the screen configuration for the first build. */
 void planAppPages(){
  float density=getResources().getDisplayMetrics().density;android.content.res.Configuration c=getResources().getConfiguration();
  float width=appAreaWidth>0?appAreaWidth:(c.screenWidthDp-2*HomeAppLayout.MARGIN_DP)*density;
  float height=appAreaHeight>0?appAreaHeight:HomeAppLayout.estimatedHeightDp(c.screenHeightDp,HomeTray.HEIGHT_DP,HomeTray.GAP_DP,HomeFrame.DOTS_DP)*density;
  float cell=appCellHeight>0?appCellHeight:HomeAppLayout.cellHeight(density,appLabel("").getLineHeight());
  appColumns=HomeAppLayout.columns(width/density);appPerPage=HomeAppLayout.perPage(width,height,cell,density);homeColumns=HomeAppLayout.homeColumns(width,height,cell,density);
 }
 /** A page measured at a size whose capacity differs from the plan re-plans the pages once from that real size. */
 void appPageMeasured(int width,int height,float cell){
  if(!HomeAppLayout.replan(appColumns,appPerPage,width,height,cell,getResources().getDisplayMetrics().density))return;
  if(width==appAreaWidth&&height==appAreaHeight&&cell==appCellHeight)return;
  appAreaWidth=width;appAreaHeight=height;appCellHeight=cell;
  handler.post(()->{if(launcherPager!=null&&!isFinishing())attachLauncherPages(true);});
 }
 /** One app page: the measured grid of real apps over the shared wallpaper, or a truthful note when none are installed. */
 View appPage(ArrayList<ResolveInfo> apps,String title,boolean noApps){
  View result;
  if(noApps){TextView none=appLabel("No other apps are installed.");none.setGravity(Gravity.CENTER);none.setMaxLines(3);result=none;}
  else{HomeAppPage grid=new HomeAppPage(this,appColumns,this::appPageMeasured);for(ResolveInfo app:apps)grid.addView(appTile(app));result=grid;}
  result.setContentDescription(title);
  result.setPadding(dp(HomeAppLayout.MARGIN_DP),dp(HomeAppLayout.TOP_DP),dp(HomeAppLayout.MARGIN_DP),0);
  result.setOnApplyWindowInsetsListener((v,i)->{v.setPadding(dp(HomeAppLayout.MARGIN_DP)+i.getSystemWindowInsetLeft(),dp(HomeAppLayout.TOP_DP)+i.getSystemWindowInsetTop(),dp(HomeAppLayout.MARGIN_DP)+i.getSystemWindowInsetRight(),i.getSystemWindowInsetBottom());return i;});
  return result;
 }
 /** The reference label: 12.5sp, white over the wallpaper with a soft dark shadow, wrapping to two lines. */
 TextView appLabel(String label){
  TextView name=new TextView(this);name.setText(label);name.setTextSize(12.5f);name.setTextColor(0xffffffff);name.setTypeface(chatFont(800));
  name.setShadowLayer(dp(3),0,dp(1),0xcc08162d);name.setIncludeFontPadding(false);name.setGravity(Gravity.TOP|Gravity.CENTER_HORIZONTAL);
  name.setMaxLines(HomeAppLayout.LABEL_LINES);name.setEllipsize(android.text.TextUtils.TruncateAt.END);
  return name;
 }
 LinearLayout makePageDots(ArrayList<String> titles){
  LinearLayout dots=new LinearLayout(this);dots.setGravity(Gravity.CENTER);dots.setOrientation(0);
  for(int i=0;i<titles.size();i++){
   final int target=i;TextView dot=text("●",10,0xffffffff);dot.setShadowLayer(dp(2),0,0,0x66101e3c);dot.setTag(i);dot.setGravity(Gravity.CENTER);dot.setAlpha(i==0?1f:.45f);
   dot.setContentDescription("Go to "+titles.get(i).toLowerCase(Locale.ROOT));dot.setFocusable(true);dot.setOnClickListener(v->launcherPager.showPage(target,true));
   dots.addView(dot,new LinearLayout.LayoutParams(dp(HomeFrame.DOTS_DP),dp(HomeFrame.DOTS_DP)));pageDots.add(dot);
  }
  return dots;
 }
 /** A real app's cell: its own icon in the 64dp tile and its label, opening it (WorkspaceLauncher). */
 View appTile(ResolveInfo app){
  LinearLayout tile=new LinearLayout(this);tile.setOrientation(1);tile.setGravity(Gravity.TOP|Gravity.CENTER_HORIZONTAL);tile.setPadding(dp(2),dp(HomeAppLayout.CELL_PAD_DP),dp(2),dp(HomeAppLayout.CELL_PAD_DP));
  String originalLabel=AppLabels.label(getPackageManager(),app.activityInfo.packageName,app.loadLabel(getPackageManager()).toString());
  final String label=originalLabel;tile.setContentDescription(label);tile.setClickable(true);tile.setFocusable(true);
  tile.setOnClickListener(v->WorkspaceLauncher.open(this,app,label));
  FrameLayout iconFrame=new FrameLayout(this);iconFrame.setBackground(raised(PixelAppearance.color(0xfffff7e5),PixelAppearance.color(0xff789ab5),18));
  int iconResource=AppLabels.icon(getPackageManager(),app.activityInfo.packageName);
  ImageView icon=new ImageView(this);icon.setImageDrawable(iconResource==0?app.loadIcon(getPackageManager()):getDrawable(iconResource));icon.setScaleType(ImageView.ScaleType.FIT_CENTER);icon.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
  iconFrame.addView(icon,new FrameLayout.LayoutParams(dp(HomeAppLayout.ICON_DP),dp(HomeAppLayout.ICON_DP),Gravity.CENTER));
  tile.addView(iconFrame,new LinearLayout.LayoutParams(dp(HomeAppLayout.TILE_DP),dp(HomeAppLayout.TILE_DP)));
  TextView name=appLabel(label);name.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
  LinearLayout.LayoutParams labelParams=new LinearLayout.LayoutParams(-1,HomeAppLayout.LABEL_LINES*name.getLineHeight());labelParams.topMargin=dp(HomeAppLayout.LABEL_GAP_DP);tile.addView(name,labelParams);
  return tile;
 }

 String audioLabel(VoiceService s){if(!s.communicating)return "Microphone off";AudioDeviceInfo d=s.audio==null?null:s.audio.getCommunicationDevice();if(d==null)return "Connecting audio…";if(VoiceService.isBluetooth(d))return "Glasses";if(d.getType()==AudioDeviceInfo.TYPE_BUILTIN_SPEAKER)return "Phone speaker";if(d.getType()==AudioDeviceInfo.TYPE_BUILTIN_EARPIECE)return "Phone earpiece";return d.getProductName().toString();}
 final Runnable tick=new Runnable(){public void run(){refresh();handler.postDelayed(this,250);}};
 void refresh(){
  refreshHomeRose(HomeRose.shown(screenHostActive,homeFrame!=null&&homeFrame.getParent()!=null));
  if(drawer||micButton==null)return;syncVisual();VoiceService s=VoiceService.instance;boolean active=s!=null&&s.live&&s.connected,starting=s!=null&&s.communicating&&!active,isMuted=active&&s.muted;
  String error=s!=null&&!active&&!starting&&s.status.toLowerCase(java.util.Locale.ROOT).matches(".*(failed|unavailable|not ready|denied|error|timed out).*")?"Couldn’t connect. Tap to retry.":"";
  if(active||starting||!error.isEmpty())startFeedbackUntil=0;
  starting=starting||SystemClock.uptimeMillis()<startFeedbackUntil;
  renderVoice(active,starting,isMuted,error,s==null?0:s.level);
  updateText(taskModel,s==null?"Model connecting…":s.guardianName.isEmpty()?s.taskModelLabel:s.guardianName+"’s Guardian");
  if(taskCard!=null)taskCard.render(s==null?"{\"current\":null}":s.taskStateJson,s!=null&&s.taskStateAvailable&&(s.taskStateStreamConnected||SystemClock.elapsedRealtime()-s.taskStateReceivedAt<5000),screenHostActive&&hasWindowFocus()&&(launcherPager==null||launcherPager.currentPage()==voicePage)&&visualDetail==null&&screenDetail==null);
  if(s==null){task.setVisibility(View.GONE);return;}updateText(guardianName,s.guardianName.isEmpty()?(experimentalHome?"":"✦ Pixel"):s.guardianName+"’s Guardian");guardianName.setTextSize(s.guardianName.isEmpty()?28:18);guardianBack.setVisibility(s.guardianName.isEmpty()?View.GONE:View.VISIBLE);if(experimentalHome&&s.conversationRevision!=seenConversation){renderConversation(s.conversationJson);seenConversation=s.conversationRevision;}updateText(route,audioLabel(s));updateText(connection,active?"Voice connected":starting?"Connecting voice…":"Voice off");send.setEnabled(!s.typedSending);composer.setEnabled(!s.typedSending);
  if(s.typedFinished!=seenTypedFinished){seenTypedFinished=s.typedFinished;if(s.typedError.isEmpty()&&composer.getText().toString().trim().equals(s.typedLastSent)){composer.setText("");saveDraft();}}
  String progress=s.typedSending?"Sending…":!s.typedError.isEmpty()?s.typedError:s.task;task.setVisibility(s.typedSending||!s.typedError.isEmpty()?View.VISIBLE:View.GONE);updateText(task,progress);
 }
 void beginEnding(boolean cancel){endingLabel=cancel?"Canceling…":"Ending…";endFeedbackUntil=SystemClock.uptimeMillis()+420;if(end!=null)end.animate().alpha(.58f).setDuration(140).start();renderVoice(false,false,false,"",0);}
 void stateHaptic(int effect){if(micButton!=null&&micButton.isShown()&&hasWindowFocus())micButton.performHapticFeedback(effect);}
 void renderVoice(boolean active,boolean starting,boolean isMuted,String error,float level){
  // End feedback belongs to the tap, not to any subsequently active session.
  boolean ending=!endingLabel.isEmpty()&&SystemClock.uptimeMillis()<endFeedbackUntil;
  if(!ending)endingLabel="";
  String phase=ending?endingLabel:starting?"connecting":active?(isMuted?"muted":"live"):error.isEmpty()?"idle":"error";
  if(!phase.equals(lastFeedbackState)&&!lastFeedbackState.isEmpty()){
   if(phase.equals("live")&&lastFeedbackState.equals("connecting"))stateHaptic(Build.VERSION.SDK_INT>=30?HapticFeedbackConstants.CONFIRM:HapticFeedbackConstants.VIRTUAL_KEY);
   else if((phase.equals("muted")&&lastFeedbackState.equals("live"))||(phase.equals("live")&&lastFeedbackState.equals("muted"))||phase.equals("idle")&&(lastFeedbackState.equals("Ending…")||lastFeedbackState.equals("Canceling…")))stateHaptic(HapticFeedbackConstants.CLOCK_TICK);
  }
  lastFeedbackState=phase;
  String label=ending?endingLabel:starting?"Connecting…":active?(isMuted?"Unmute":"Mute"):error.isEmpty()?"Start":"Retry";
  micButton.update(label,active&&!isMuted&&!ending,level);micButton.setEnabled(!starting&&!ending);
  micButton.setContentDescription(ending?endingLabel:starting?"Connecting voice":active?(isMuted?"Unmute microphone":"Mute microphone"):error.isEmpty()?"Start session":"Retry connection");
  micButton.setStateDescription(active?(isMuted?"Microphone muted":"Microphone on"):"Microphone off");
  updateText(state,ending?endingLabel:starting?"Connecting…":active?(isMuted?"Muted":"Listening"):error.isEmpty()?"Tap to talk":"Try again");
  updateText(hint,error);hint.setVisibility(error.isEmpty()?View.GONE:View.VISIBLE);
  endSlot.setVisibility(active||starting||ending?View.VISIBLE:View.GONE);end.setEnabled(!ending&&(active||starting));if(!ending){end.animate().cancel();end.setAlpha(1f);}
  updateText(end,"×");end.setContentDescription(ending?endingLabel:starting?"Cancel connection":"End session");
 }
 @Override protected void onResume(){super.onResume();screenHostActive=true;homeStopped=false;ScreenPolicy.prefs(this).registerOnSharedPreferenceChangeListener(screenPolicyChanged);ScreenPolicy.apply(this,true);if(macPanel!=null)macPanel.resume();if(clockWidget!=null&&launcherPager!=null)clockWidget.setActive(launcherPager.currentPage()==widgetsPage);if(weatherWidget!=null&&launcherPager!=null)weatherWidget.setActive(launcherPager.currentPage()==widgetsPage);if(galleryWidget!=null&&launcherPager!=null)galleryWidget.setActive(launcherPager.currentPage()==widgetsPage);handler.removeCallbacks(tick);handler.post(tick);}
 @Override protected void onStop(){super.onStop();homeStopped=true;}
 /** The shown page survives recreation (HomePages.restore). */
 @Override protected void onSaveInstanceState(Bundle out){super.onSaveInstanceState(out);if(launcherPager!=null)out.putInt("home-page",launcherPager.currentPage());}
 @Override protected void onPause(){refreshHomeRose(false);homeOverview.homeLeft();ScreenPolicy.prefs(this).unregisterOnSharedPreferenceChangeListener(screenPolicyChanged);ScreenPolicy.apply(this,false);screenHostActive=false;if(clockWidget!=null)clockWidget.setActive(false);if(weatherWidget!=null)weatherWidget.setActive(false);if(galleryWidget!=null)galleryWidget.setActive(false);if(macPanel!=null)macPanel.pause();saveDraft();handler.removeCallbacks(tick);super.onPause();}
 @Override public void onNewIntent(Intent i){super.onNewIntent(i);int previous=launcherPager==null?-1:launcherPager.currentPage();boolean homeShown=!homeStopped;if(!reuseHome(i))home();showHomeFor(i,true,previous,homeShown);}
 /** What the built Home, pager and catalog came from: package changes, screen configuration and the dock's default apps. */
 String homeStamp(){android.content.res.Configuration c=getResources().getConfiguration();return HomeReuse.stamp(packageGeneration,c.screenWidthDp+"x"+c.screenHeightDp+"@"+c.densityDpi+"/"+c.orientation+"/"+c.fontScale+"/"+c.uiMode+"/"+c.getLocales().toLanguageTags(),RoseHomeView.dockRolePackages(getPackageManager(),getPackageName()));}
 /** An unchanged HOME or app-icon return keeps the built Home, pager, catalog, conversation page and draft (OS-HOME-REUSE-001). */
 boolean reuseHome(Intent i){return i!=null&&HomeReuse.reuse(HomeEntry.of(Intent.ACTION_MAIN.equals(i.getAction()),i.hasCategory(Intent.CATEGORY_HOME),i.hasCategory(Intent.CATEGORY_LAUNCHER),true,false,true).showHome,homeFrame!=null&&homeFrame.getParent()!=null,builtHomeStamp,homeStamp(),chatMode,visualDetail!=null||screenDetail!=null,experimentalHome!=getPreferences(0).getBoolean("conversation-home",true));}
 /** The same snapshot RoseConversationActivity and AgentTrayService build: no service is inactive, never guessed. */
 static ConversationState conversationState(){VoiceService s=VoiceService.instance;return s==null?ConversationState.of(false,false,false,false,false,null,null,null)
   :ConversationState.of(true,s.live,s.connected,s.muted,s.communicating,s.lastEndReason,s.lastStartError,s.backendReachable);}
 /** Home's Rose entry: App's conversation surface (APP-P2-002-HOOK-001). Plain explicit Intent in this task, so Back
  * returns Home and HOME clears it; navigation only, never a voice or task command. */
 void openRoseConversation(){startActivity(new Intent(this,RoseConversationActivity.class));}
 // The tray's overview well: the system task overview through the owner-enabled phone-controls service only (HomeOverview).
 static boolean overviewAvailable(){return PhoneAccessibility.active!=null;}
 void openOverview(){String notice=HomeOverview.notice(homeOverview.request(SystemClock.elapsedRealtime(),()->{PhoneAccessibility a=PhoneAccessibility.active;return a==null?null:a.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_RECENTS);}));if(notice!=null)Toast.makeText(this,notice,Toast.LENGTH_SHORT).show();}
 /** HOME shows Rose Home; the app icon also opens the Rose conversation over it (HomeEntry). Never changes the HOME choice or voice. */
 /** HOME returning from an app or the conversation keeps the page left (previous); HOME on Home, or the app icon, shows Home (HomePages). */
 void showHomeFor(Intent intent,boolean fresh,int previous,boolean homeShown){if(intent==null)return;HomeEntry entry=HomeEntry.of(Intent.ACTION_MAIN.equals(intent.getAction()),intent.hasCategory(Intent.CATEGORY_HOME),intent.hasCategory(Intent.CATEGORY_LAUNCHER),fresh,(intent.getFlags()&Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY)!=0,DeviceIdentity.paired(this));if(entry.showHome&&launcherPager!=null)launcherPager.showPage(HomePages.target(true,!intent.hasCategory(Intent.CATEGORY_HOME),homeShown,previous,launcherPager.currentPage(),launcherPager.getChildCount()),false);if(entry.openConversation)openRoseConversation();}
 void showHomeFor(Intent intent,boolean fresh){showHomeFor(intent,fresh,-1,true);}
 /** The tray's Rose from what VoiceService already publishes, once per refresh tick; hidden or paused stops its animation. */
 void refreshHomeRose(boolean shown){if(homeTray==null)return;VoiceService s=VoiceService.instance;ConversationState c=conversationState();homeTray.show(c,HomeRose.state(c,s==null?Float.NaN:s.trayInputLevel,s==null?Float.NaN:s.trayOutputLevel,s==null?0L:s.trayAudioObservedAt,SystemClock.elapsedRealtime(),HomeRose.reducedMotion(android.provider.Settings.Global.getFloat(getContentResolver(),android.provider.Settings.Global.ANIMATOR_DURATION_SCALE,1f)),shown));}
 @Override public void onBackPressed(){if(screenDetail!=null)closeMacScreen();else if(visualDetail!=null)closeVisual();else if(drawer&&launcherPager!=null)launcherPager.showPage(homePage,true);else if(experimentalHome&&chatMode){chatMode=false;home();}else if(launcherPager!=null)launcherPager.showPage(homePage,true);}

 boolean macScreenEnabled(){return getPreferences(0).getBoolean("mac-screen-enabled",false);}
 void showMacCard(){
  if(macPanel!=null)return;
  visualSlot.removeAllViews();
  macPanel=new MacScreenPanel(this,true,this::expandMacScreen,()->{if(screenDetail!=null)closeMacScreen();else{getPreferences(0).edit().putBoolean("mac-screen-enabled",false).apply();home();}});
  macPanel.setBackground(shape(paper,24));macPanel.setClipToOutline(true);visualSlot.addView(macPanel,new FrameLayout.LayoutParams(-1,-2));visualSlot.setVisibility(View.VISIBLE);visualSlot.postDelayed(()->{if(micButton!=null)micButton.invalidate();if(state!=null)state.invalidate();},350);
  if(screenHostActive)macPanel.resume();
 }
 void expandMacScreen(){
  if(macPanel==null||screenDetail!=null)return;
  visualSlot.removeView(macPanel);FrameLayout overlay=new FrameLayout(this);overlay.setBackground(new PlayfulTheme.World());overlay.setPadding(page.getPaddingLeft(),page.getPaddingTop(),page.getPaddingRight(),page.getPaddingBottom());
  overlay.setOnApplyWindowInsetsListener((v,i)->{v.setPadding(page.getPaddingLeft(),page.getPaddingTop(),page.getPaddingRight(),Math.max(page.getPaddingBottom(),i.getInsets(WindowInsets.Type.ime()).bottom));return i;});overlay.addView(macPanel,new FrameLayout.LayoutParams(-1,-1));macPanel.setCompact(false);addContentView(overlay,new ViewGroup.LayoutParams(-1,-1));screenDetail=overlay;page.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
 }
 void closeMacScreen(){
  if(screenDetail==null)return;
  ((android.view.inputmethod.InputMethodManager)getSystemService(INPUT_METHOD_SERVICE)).hideSoftInputFromWindow(macPanel.getWindowToken(),0);
  screenDetail.removeView(macPanel);if(screenDetail.getParent() instanceof ViewGroup)((ViewGroup)screenDetail.getParent()).removeView(screenDetail);screenDetail=null;
  macPanel.setCompact(true);visualSlot.addView(macPanel,new FrameLayout.LayoutParams(-1,-2));page.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_AUTO);
 }
 void destroyMacScreen(){
  if(macPanel!=null){macPanel.destroy();macPanel=null;}
  if(screenDetail!=null&&screenDetail.getParent() instanceof ViewGroup)((ViewGroup)screenDetail.getParent()).removeView(screenDetail);screenDetail=null;
 }
 @Override protected void onDestroy(){if(homeTray!=null)homeTray.release();if(taskCard!=null)taskCard.dispose();if(clockWidget!=null)clockWidget.setActive(false);if(weatherWidget!=null)weatherWidget.setActive(false);if(galleryWidget!=null)galleryWidget.setActive(false);if(launcherReceiverRegistered){unregisterReceiver(launcherPackages);launcherReceiverRegistered=false;}destroyMacScreen();super.onDestroy();}
 class MicControl extends View {
  final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);String label="Start";boolean listening;float targetLevel,displayLevel;long lastFrame;
  MicControl(){super(MainActivity.this);setClickable(true);setFocusable(true);setContentDescription("Start session");}
  void update(String next,boolean on,float level){float nextLevel=on?Math.max(0,Math.min(1,level/100f)):0;boolean changed=!label.equals(next)||listening!=on||targetLevel!=nextLevel;label=next;listening=on;targetLevel=nextLevel;if(!on)displayLevel=0;if(changed)invalidate();}
  @Override public CharSequence getAccessibilityClassName(){return Button.class.getName();}
  @Override public void onInitializeAccessibilityNodeInfo(AccessibilityNodeInfo info){super.onInitializeAccessibilityNodeInfo(info);info.setText(label);}
  @Override public boolean onTouchEvent(MotionEvent event){if(event.getActionMasked()==MotionEvent.ACTION_DOWN&&isEnabled())performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);return super.onTouchEvent(event);}
  @Override protected void drawableStateChanged(){super.drawableStateChanged();invalidate();}
  @Override public boolean performClick(){return super.performClick();}
  @Override protected void onDraw(Canvas c){
   super.onDraw(c);long now=SystemClock.uptimeMillis();float dt=lastFrame==0?16:Math.min(64,now-lastFrame);lastFrame=now;
   float difference=targetLevel-displayLevel;displayLevel+=difference*(1-(float)Math.exp(-dt/(difference>0?65f:180f)));if(Math.abs(targetLevel-displayLevel)<.002f)displayLevel=targetLevel;
   float cx=getWidth()/2f,cy=getHeight()/2f,r=Math.min(cx,cy)-dp(16);
   paint.setStyle(Paint.Style.FILL);paint.setShader(null);
   if(listening){paint.setColor(PixelAppearance.color(0xffd5e7f2));c.drawCircle(cx,cy,r+dp(7),paint);}
   paint.setColor(PixelAppearance.color(0xffb3d0e4));c.drawCircle(cx,cy+dp(7),r+dp(4),paint);
   cy+=isPressed()?dp(3):0;
   paint.setColor(PixelAppearance.color(0xfffff7e5));c.drawCircle(cx,cy,r+dp(4),paint);
   int top=listening?0xffffe49b:label.equals("Unmute")?PixelAppearance.color(0xffffc0af):PixelAppearance.color(0xffb9dff3);
   int bottom=listening?0xffffce60:label.equals("Unmute")?PixelAppearance.color(0xfff58b78):PixelAppearance.color(0xff8cc5e3);
   paint.setShader(new LinearGradient(0,cy-r,0,cy+r,top,bottom,Shader.TileMode.CLAMP));c.drawCircle(cx,cy,r,paint);paint.setShader(null);paint.setAlpha(255);
   paint.setStyle(Paint.Style.STROKE);paint.setStrokeWidth(dp(2));paint.setStrokeCap(Paint.Cap.ROUND);paint.setColor(0x55ffffff);c.drawArc(cx-r+dp(9),cy-r+dp(9),cx+r-dp(9),cy+r-dp(9),215,105,false,paint);
   if(label.equals("Connecting…")){paint.setColor(0xffffd66b);paint.setStrokeWidth(dp(3));float angle=(now%1800)*360f/1800;c.drawArc(cx-r+dp(8),cy-r+dp(8),cx+r-dp(8),cy+r-dp(8),angle,64,false,paint);postInvalidateOnAnimation();}
   if(label.equals("Ending…")||label.equals("Canceling…")){float remaining=Math.max(0,Math.min(1,(endFeedbackUntil-now)/420f));paint.setColor(PixelAppearance.color(0xff789ab5));paint.setStrokeWidth(dp(3));c.drawArc(cx-r+dp(8),cy-r+dp(8),cx+r-dp(8),cy+r-dp(8),-90,remaining*280,false,paint);if(remaining>0)postInvalidateOnAnimation();}
   float my=cy-dp(3);paint.setColor(listening?0xff593f35:ink);paint.setStrokeWidth(dp(3));
   c.drawRoundRect(cx-dp(6),my-dp(14),cx+dp(6),my+dp(5),dp(6),dp(6),paint);
   c.drawArc(cx-dp(12),my-dp(4),cx+dp(12),my+dp(13),0,180,false,paint);
   c.drawLine(cx,my+dp(13),cx,my+dp(20),paint);c.drawLine(cx-dp(6),my+dp(20),cx+dp(6),my+dp(20),paint);
   if(label.equals("Unmute")){paint.setStrokeWidth(dp(3));c.drawLine(cx-dp(17),my-dp(16),cx+dp(17),my+dp(19),paint);}
   if(listening){paint.setStrokeWidth(dp(3));for(int i=0;i<3;i++){float length=dp(2+displayLevel*(7-i*2));float x=dp(27+i*7);c.drawLine(cx-x,my-length,cx-x,my+length,paint);c.drawLine(cx+x,my-length,cx+x,my+length,paint);}}
   paint.setStyle(Paint.Style.FILL);paint.setTextAlign(Paint.Align.CENTER);paint.setTypeface(Typeface.create("sans-serif-rounded",Typeface.BOLD));paint.setTextSize(dp(17));
   if(listening&&displayLevel!=targetLevel)postInvalidateOnAnimation();
  }
 }
}
