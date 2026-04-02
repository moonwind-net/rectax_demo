declare module "*.vue" {
  import type { DefineComponent } from "vue";
  const component: DefineComponent<object, object, any>;
  export default component;
}

declare const __APP_VERSION__: string;
declare const __BUILD_TIME__: string;