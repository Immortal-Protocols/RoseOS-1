/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.systemui.roseshade;

import com.android.systemui.CoreStartable;

import dagger.Binds;
import dagger.Module;
import dagger.multibindings.ClassKey;
import dagger.multibindings.IntoMap;

/** Binds the Rose notification shade's startable (1G); included by ReferenceSystemUIModule (rose-shade-systemui.patch). */
@Module
public abstract class RoseShadeModule {
    @Binds
    @IntoMap
    @ClassKey(RoseShadeStartable.class)
    public abstract CoreStartable bindRoseShadeStartable(RoseShadeStartable startable);
}
