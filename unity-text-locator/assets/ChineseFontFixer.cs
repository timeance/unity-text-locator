using System;
using System.Collections;
using System.IO;
using System.Reflection;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

public static class ChineseFontBootstrap
{
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    public static void Initialize()
    {
        if (GameObject.Find("ChineseFontFixer_Runtime") != null)
        {
            return;
        }
        GameObject go = new GameObject("ChineseFontFixer_Runtime");
        UnityEngine.Object.DontDestroyOnLoad(go);
        go.hideFlags = HideFlags.HideAndDontSave;
        go.AddComponent<ChineseFontFixerRuntime>();
    }
}

public sealed class ChineseFontFixerRuntime : MonoBehaviour
{
    private Font legacyFont;
    private TMP_FontAsset fallbackFont;
    private float nextSlowPass;
    private static readonly string[] FontFileNames =
    {
        "msyh.ttc",
        "msyh.ttf",
        "NotoSansSC-VF.ttf",
        "simhei.ttf",
        "simsun.ttc",
        "Deng.ttf"
    };
    private static readonly string[] FontPaths = BuildFontPaths();

    private static string[] BuildFontPaths()
    {
        string directory = Environment.GetFolderPath(Environment.SpecialFolder.Fonts);
        if (string.IsNullOrEmpty(directory))
        {
            directory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "Fonts");
        }
        string[] paths = new string[FontFileNames.Length];
        for (int i = 0; i < FontFileNames.Length; i++)
        {
            paths[i] = Path.Combine(directory, FontFileNames[i]);
        }
        return paths;
    }

    private IEnumerator Start()
    {
        CreateFonts();
        ApplyTMPFallback();
        for (int i = 0; i < 40; i++)
        {
            RefreshMissingGlyphs();
            yield return new WaitForSecondsRealtime(0.25f);
        }
    }

    private void Update()
    {
        if (Time.unscaledTime >= nextSlowPass)
        {
            nextSlowPass = Time.unscaledTime + 2f;
            RefreshMissingGlyphs();
        }
    }

    private void CreateFonts()
    {
        if (legacyFont == null)
        {
            legacyFont = CreateLegacyFont();
            if (legacyFont != null)
            {
                legacyFont.name = "ChineseFontFixer_OSFont";
            }
        }
        if (fallbackFont == null && legacyFont != null)
        {
            fallbackFont = CreateDynamicTMPFontAsset(legacyFont);
            if (fallbackFont != null)
            {
                fallbackFont.name = "ChineseFontFixer_TMPFallback";
                TrySetProperty(fallbackFont, "atlasPopulationMode", "Dynamic");
                TrySetProperty(fallbackFont, "multiAtlasTextures", true);
                Debug.Log("[ChineseFontFixer] TMP fallback/UI missing-glyph Chinese font initialized.");
            }
        }
    }

    private Font CreateLegacyFont()
    {
        for (int i = 0; i < FontPaths.Length; i++)
        {
            if (!File.Exists(FontPaths[i]))
            {
                continue;
            }
            try
            {
                Font candidate = new Font(FontPaths[i]);
                if (candidate != null && candidate.HasCharacter('\u4e2d'))
                {
                    return candidate;
                }
            }
            catch (Exception ex)
            {
                Debug.LogWarning("[ChineseFontFixer] Font path failed: " + FontPaths[i] + " " + ex.Message);
            }
        }
        Debug.LogWarning("[ChineseFontFixer] No usable CJK system font file found.");
        return null;
    }

    private TMP_FontAsset CreateDynamicTMPFontAsset(Font font)
    {
        MethodInfo[] methods = typeof(TMP_FontAsset).GetMethods(BindingFlags.Public | BindingFlags.Static);
        for (int pass = 0; pass < 2; pass++)
        {
            for (int i = 0; i < methods.Length; i++)
            {
                MethodInfo method = methods[i];
                if (method.Name != "CreateFontAsset")
                {
                    continue;
                }
                ParameterInfo[] p = method.GetParameters();
                try
                {
                    if (pass == 0 && p.Length >= 7 && p[0].ParameterType == typeof(Font))
                    {
                        object[] args = new object[p.Length];
                        args[0] = font;
                        args[1] = 90;
                        args[2] = 9;
                        args[3] = Enum.Parse(p[3].ParameterType, "SDFAA");
                        args[4] = 4096;
                        args[5] = 4096;
                        args[6] = Enum.Parse(p[6].ParameterType, "Dynamic");
                        for (int j = 7; j < p.Length; j++)
                        {
                            args[j] = p[j].ParameterType == typeof(bool)
                                ? (object)true
                                : (p[j].ParameterType.IsValueType ? Activator.CreateInstance(p[j].ParameterType) : null);
                        }
                        return method.Invoke(null, args) as TMP_FontAsset;
                    }
                    if (pass == 1 && p.Length == 1 && p[0].ParameterType == typeof(Font))
                    {
                        return method.Invoke(null, new object[] { font }) as TMP_FontAsset;
                    }
                }
                catch (Exception ex)
                {
                    Debug.LogWarning("[ChineseFontFixer] CreateFontAsset overload failed: " + ex.Message);
                }
            }
        }
        return null;
    }

    private void ApplyTMPFallback()
    {
        if (fallbackFont == null)
        {
            return;
        }
        try
        {
            if (TMP_Settings.fallbackFontAssets != null && !TMP_Settings.fallbackFontAssets.Contains(fallbackFont))
            {
                TMP_Settings.fallbackFontAssets.Insert(0, fallbackFont);
            }
        }
        catch
        {
        }
    }

    private void RefreshMissingGlyphs()
    {
        CreateFonts();
        ApplyTMPFallback();
        if (fallbackFont != null)
        {
            TMP_Text[] texts = Resources.FindObjectsOfTypeAll<TMP_Text>();
            for (int i = 0; i < texts.Length; i++)
            {
                TMP_Text text = texts[i];
                if (text == null || string.IsNullOrEmpty(text.text) || !ContainsCjk(text.text))
                {
                    continue;
                }
                TryAddCharacters(fallbackFont, text.text);
                text.SetVerticesDirty();
                text.SetLayoutDirty();
                text.SetMaterialDirty();
                text.ForceMeshUpdate();
            }
        }
        if (legacyFont != null)
        {
            Text[] texts = Resources.FindObjectsOfTypeAll<Text>();
            for (int i = 0; i < texts.Length; i++)
            {
                Text text = texts[i];
                if (text != null && NeedsLegacyFallback(text))
                {
                    text.font = legacyFont;
                    text.SetVerticesDirty();
                    text.SetLayoutDirty();
                    text.SetMaterialDirty();
                }
            }
        }
    }

    private bool NeedsLegacyFallback(Text text)
    {
        if (string.IsNullOrEmpty(text.text) || !ContainsCjk(text.text) || text.font == legacyFont)
        {
            return false;
        }
        for (int i = 0; i < text.text.Length; i++)
        {
            char character = text.text[i];
            if (IsCjk(character) && (text.font == null || !text.font.HasCharacter(character)))
            {
                return true;
            }
        }
        return false;
    }

    private static bool ContainsCjk(string text)
    {
        for (int i = 0; i < text.Length; i++)
        {
            if (IsCjk(text[i]))
            {
                return true;
            }
        }
        return false;
    }

    private static bool IsCjk(char character)
    {
        return (character >= '\u3400' && character <= '\u9fff')
            || (character >= '\u3040' && character <= '\u30ff')
            || (character >= '\uff66' && character <= '\uff9f');
    }

    private static void TryAddCharacters(TMP_FontAsset asset, string characters)
    {
        MethodInfo[] methods = asset.GetType().GetMethods(BindingFlags.Public | BindingFlags.Instance);
        for (int i = 0; i < methods.Length; i++)
        {
            MethodInfo method = methods[i];
            ParameterInfo[] p = method.GetParameters();
            if (method.Name != "TryAddCharacters" || p.Length == 0 || p[0].ParameterType != typeof(string))
            {
                continue;
            }
            try
            {
                object[] args = new object[p.Length];
                args[0] = characters;
                for (int j = 1; j < p.Length; j++)
                {
                    Type type = p[j].ParameterType;
                    args[j] = type.IsByRef
                        ? null
                        : (type == typeof(bool)
                            ? (object)false
                            : (type.IsValueType ? Activator.CreateInstance(type) : null));
                }
                method.Invoke(asset, args);
                return;
            }
            catch
            {
            }
        }
    }

    private static void TrySetProperty(object target, string propertyName, object value)
    {
        try
        {
            PropertyInfo property = target.GetType().GetProperty(propertyName, BindingFlags.Public | BindingFlags.Instance);
            if (property == null || !property.CanWrite)
            {
                return;
            }
            if (property.PropertyType.IsEnum && value is string)
            {
                property.SetValue(target, Enum.Parse(property.PropertyType, (string)value), null);
            }
            else
            {
                property.SetValue(target, value, null);
            }
        }
        catch
        {
        }
    }
}
