package dev.pixel.backgroundcontrol;

public final class InstallerRecoveryPolicyTest {
    static void check(boolean expected, boolean session, String target, boolean blocked,
            int reason, String blocker) {
        if (InstallerRecoveryPolicy.shouldRequest(session,target,blocked,reason,blocker,2)!=expected)
            throw new AssertionError("Unexpected recovery policy");
    }
    public static void main(String[] args) {
        check(true,true,"com.android.vending",true,2,"com.android.packageinstaller");
        check(false,false,"com.android.vending",true,2,"com.android.packageinstaller");
        check(false,true,"com.android.vending",false,2,"com.android.packageinstaller");
        check(false,true,"com.example.bank",true,2,"com.android.packageinstaller");
        check(false,true,"com.android.vending",true,1,"com.android.packageinstaller");
        check(false,true,"com.android.vending",true,3,"com.android.packageinstaller");
        check(false,true,"com.android.vending",true,2,"com.android.permissioncontroller");
        check(false,true,"com.android.vending",true,2,null);
        System.out.println("InstallerRecoveryPolicyTest: 8 checks passed");
    }
}
