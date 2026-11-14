package android.os; public class Binder implements IBinder { public void linkToDeath(DeathRecipient r, int f) {} public boolean unlinkToDeath(DeathRecipient r, int f) { return true; } }
