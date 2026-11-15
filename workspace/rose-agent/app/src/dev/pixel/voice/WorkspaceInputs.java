package dev.pixel.voice;

import java.util.Collections;
import java.util.List;

/**
 * Project 2: where the workspace's notifications and content come from. These are the snapshot's inputs beside
 * apps, shortcuts, templates and tasks (WorkspaceSnapshot.build).
 *
 * <ul>
 *   <li><b>The phone's source is WorkspaceDeviceInputs.</b> It gives notification titles and media sessions only
 *   after the person has turned on Notification access for Rose Agent in Settings and accepted the workspace's
 *   own disclosure (WorkspaceNotices.status). Otherwise it is not_granted and gives none.</li>
 *   <li><b>Content is media only.</b> Rose Agent has no other content source. Documents, contacts, places and
 *   recipes would each need a new source and permission, so none is offered. The HiFi's examples are sample data,
 *   and Rose's book, podcast and sky hand-offs are not repurposed.</li>
 * </ul>
 *
 * NONE is the source with nothing: notifications not_decided and content no_source. A source counts only when it
 * says available, and one that throws gives none, so nothing is ever fabricated. Pure.
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

 /** The pure default, for code and tests without a phone. The phone reads WorkspaceDeviceInputs instead. */
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
