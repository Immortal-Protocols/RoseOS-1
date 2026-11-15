package dev.pixel.voice;

import java.util.Collections;
import java.util.List;

/**
 * Project 2: where the workspace's notifications and content come from. These are the snapshot's inputs beside
 * apps, shortcuts, templates and tasks (WorkspaceSnapshot.build).
 *
 * <ul>
 *   <li><b>Notifications: none, because D1 is undecided.</b> No source on this phone may read them. Rose Agent has
 *   no notification listener and holds no notification-access grant. The Rose shade (OS Core's SystemUI) reads
 *   them inside SystemUI only and offers Rose Agent nothing. D1 is Nathan's choice:
 *   <ul>
 *     <li>(a) a listener in Rose Agent, behind the person's own notification-access grant;</li>
 *     <li>(b) a bounded, signature-protected snapshot from the Rose shade, which is OS Core's, with a security
 *     review;</li>
 *     <li>(c) none.</li>
 *   </ul>
 *   With the compose route on, any notification titles in the snapshot go to the owner's Rose server, so the
 *   choice includes that.</li>
 *   <li><b>Content: none, because no source exists.</b> The HiFi's recipes, documents and media are sample data.
 *   Rose's book, podcast and sky entries are hand-offs the person starts for a conversation, not a standing
 *   feed, and are not repurposed here.</li>
 * </ul>
 *
 * Until a source is chosen, NONE is used. The snapshot then has empty notifications and content, which the
 * composer reads as "there are none". The status says why, and never pretends a source exists. Pure.
 */
final class WorkspaceInputs {
 private WorkspaceInputs() {}

 /** No source is decided (D1) or exists (content). */
 static final String NOT_DECIDED="not_decided",NO_SOURCE="no_source";
 /** A source exists but the person has not granted it, or it is turned off. */
 static final String NOT_GRANTED="not_granted";
 /** A source exists and gave its current items (possibly none). */
 static final String AVAILABLE="available";

 /** One source of notifications and content. Read on the page's worker thread; it must not block for long. */
 interface Source {
  String notificationStatus();
  List<WorkspaceSnapshot.Notice> notices();
  String contentStatus();
  List<WorkspaceSnapshot.Content> content();
 }

 /** Today's source: none decided for notifications, none existing for content. */
 static final Source NONE=new Source(){
  public String notificationStatus(){return NOT_DECIDED;}
  public List<WorkspaceSnapshot.Notice> notices(){return Collections.emptyList();}
  public String contentStatus(){return NO_SOURCE;}
  public List<WorkspaceSnapshot.Content> content(){return Collections.emptyList();}
 };

 /** The source the phone reads: NONE until D1 and a content source are decided. */
 static final Source SOURCE=NONE;

 /**
  * The items a source may give now. Anything but AVAILABLE gives none, and a source that throws gives none,
  * so a broken or ungranted source never fabricates items.
  */
 static List<WorkspaceSnapshot.Notice> notices(Source s){
  try{return s!=null&&AVAILABLE.equals(s.notificationStatus())&&s.notices()!=null?s.notices():Collections.<WorkspaceSnapshot.Notice>emptyList();}
  catch(RuntimeException e){return Collections.emptyList();}
 }
 static List<WorkspaceSnapshot.Content> content(Source s){
  try{return s!=null&&AVAILABLE.equals(s.contentStatus())&&s.content()!=null?s.content():Collections.<WorkspaceSnapshot.Content>emptyList();}
  catch(RuntimeException e){return Collections.emptyList();}
 }
}
