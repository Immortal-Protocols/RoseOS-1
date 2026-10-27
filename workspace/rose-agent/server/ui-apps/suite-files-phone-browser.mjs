// Integration entry for the Files, Phone, and Vanadium complete-operation adapters.
// The caller supplies the existing authorized PhoneCommands transport.
import {createFilesAdapter} from './suite-files.mjs';
import {createPhoneAdapter} from './suite-phone.mjs';
import {createBrowserAdapter} from './suite-browser.mjs';
export {createFilesAdapter,filesTools} from './suite-files.mjs';
export {createPhoneAdapter,phoneTools} from './suite-phone.mjs';
export {createBrowserAdapter,browserTools,browserSearchTool} from './suite-browser.mjs';
export const createSuiteAdapters=(phone,{enableBrowserSearch=false}={})=>[createFilesAdapter(phone),createPhoneAdapter(phone),createBrowserAdapter(phone,{enableSearch:enableBrowserSearch})];
