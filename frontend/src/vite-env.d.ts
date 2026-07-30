/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AMAP_JSAPI_KEY: string
  readonly VITE_AMAP_SECURITY_JS_CODE: string
  readonly VITE_AMAP_SERVICE_HOST: string
}

interface Window {
  _AMapSecurityConfig?: {
    securityJsCode?: string
    serviceHost?: string
  }
}
