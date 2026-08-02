import type { ThemeConfig } from 'antd'

export const theme: ThemeConfig = {
  token: {
    colorPrimary: '#b7791f',
    colorInfo: '#4d6b9b',
    colorSuccess: '#287a55',
    colorWarning: '#b96e12',
    colorError: '#c94242',
    colorTextBase: '#2b241a',
    colorBgBase: '#fffdf8',
    colorBgLayout: '#f5f1e8',
    colorBorder: '#e6dcc8',
    colorBorderSecondary: '#eee5d5',
    borderRadius: 8,
    borderRadiusLG: 8,
    borderRadiusSM: 6,
    fontSize: 14,
    fontSizeLG: 16,
    fontSizeSM: 12,
    lineHeight: 1.6,
    controlHeight: 36,
    controlHeightLG: 42,
    controlHeightSM: 30,
    fontFamily:
      "'PingFang SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Microsoft YaHei', sans-serif",
    boxShadow:
      '0 16px 36px -30px rgba(77, 55, 21, 0.42), 0 1px 2px rgba(77, 55, 21, 0.04)',
    boxShadowSecondary:
      '0 22px 52px -38px rgba(77, 55, 21, 0.48), 0 1px 2px rgba(77, 55, 21, 0.04)',
  },
  components: {
    Layout: {
      siderBg: '#fffdf8',
      headerBg: '#fffdf8',
      headerHeight: 64,
      bodyBg: '#f5f1e8',
    },
    Menu: {
      itemBg: 'transparent',
      itemSelectedBg: '#faebc9',
      itemSelectedColor: '#7d5010',
      itemHoverBg: '#fff6e3',
      itemHoverColor: '#2b241a',
      itemColor: '#6f624f',
      itemHeight: 40,
      itemMarginInline: 8,
      itemMarginBlock: 3,
      itemBorderRadius: 10,
      fontSize: 14,
      groupTitleColor: '#978973',
      groupTitleFontSize: 12,
    },
    Card: {
      borderRadiusLG: 8,
      paddingLG: 20,
      headerFontSize: 16,
      headerFontSizeSM: 15,
    },
    Button: {
      controlHeight: 36,
      borderRadius: 10,
      fontWeight: 500,
      primaryShadow: 'none',
    },
    Input: {
      borderRadius: 8,
      fontSize: 14,
      paddingInline: 12,
    },
    Select: {
      borderRadius: 8,
    },
    Modal: {
      borderRadiusLG: 12,
      titleFontSize: 17,
      headerBg: '#ffffff',
      paddingMD: 22,
      paddingContentHorizontalLG: 22,
    },
    Drawer: {
      colorBgElevated: '#ffffff',
    },
    Table: {
      headerBg: '#faf6ed',
      headerColor: '#6f624f',
      borderColor: '#eee5d5',
    },
    Tabs: {
      itemSelectedColor: '#7d5010',
      inkBarColor: '#b7791f',
    },
  },
}
