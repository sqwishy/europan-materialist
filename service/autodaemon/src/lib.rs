/* lots of code ripped from autotrait by fasterthanlime */
#![allow(unused)]

use quote::{TokenStreamExt as _, format_ident, quote};
use unsynn::*;

keyword! {
    KImpl = "impl";
    KPub = "pub";
    KAsync = "async";
    KFn = "fn";
    KMut = "mut";
    KSelf = "self";
}

operator! {
    RightArrow = "->";
    DoubleColon = "::";
    SingleQuote = "'";
}

unsynn! {
    // struct Attrs { }

    struct ImplBlock {
        _impl: KImpl,
        typ_name: Ident,
        body: BraceGroupContaining<Vec<Function>>,
    }

    struct Function {
        _pub: Option<KPub>,
        // _async: Option<KAsync>,
        _fn: KFn,
        name: Ident,
        params: ParenthesisGroupContaining<Params>,
        ret: Cons<RightArrow, Type>,
        body: BraceGroup,
    }

    struct Params {
        params: CommaDelimitedVec<Param>,
    }

    enum Param {
        ReceiverAndSelf(ReceiverAndSelf),
        NamedParam(NamedParam),
    }

    struct ReceiverAndSelf {
        _and: And,
        _mut: Option<KMut>,
        _self: KSelf,
    }

    struct NamedParam {
        _mut: Option<KMut>,
        ident: Ident,
        _colon: Colon,
        typ: Type,
    }

    enum Type {
        WithGenerics(WithGenerics),
        Tuple(TupleType),
        Simple(SimpleType),
    }

    struct SimpleType {
        ident: DelimitedVec<Ident, DoubleColon>,
    }

    struct TupleType {
        types: ParenthesisGroupContaining<CommaDelimitedVec<Type>>,
    }

    struct WithGenerics {
        typ: SimpleType,
        _lt: Lt,
        params: CommaDelimitedVec<GenericParam>,
        _gt: Gt,
    }

    enum GenericParam {
        Lifetime(Lifetime),
        Type(Box<Type>),
    }

    struct Lifetime {
        _lifetime: SingleQuote,
        ident: Ident,
    }
}

impl Param {
    fn to_named_param(&self) -> Option<&NamedParam> {
        match self {
            Param::NamedParam(p) => Some(p),
            Param::ReceiverAndSelf(p) => None,
        }
    }
}

impl quote::ToTokens for Type {
    fn to_tokens(&self, tokens: &mut TokenStream) {
        match self {
            Type::Simple(i) => quote::ToTokens::to_tokens(i, tokens),
            Type::Tuple(i) => quote::ToTokens::to_tokens(i, tokens),
            Type::WithGenerics(i) => quote::ToTokens::to_tokens(i, tokens),
        }
    }
}

impl quote::ToTokens for NamedParam {
    fn to_tokens(&self, tokens: &mut TokenStream) {
        let ident = &self.ident;
        let typ = &self.typ;
        quote::ToTokens::to_tokens(&quote! { #ident: #typ }, tokens)
    }
}

impl quote::ToTokens for SimpleType {
    fn to_tokens(&self, tokens: &mut TokenStream) {
        self.ident.to_tokens(tokens)
    }
}

impl quote::ToTokens for TupleType {
    fn to_tokens(&self, tokens: &mut TokenStream) {
        let types = self.types.content.iter().map(|typ| &typ.value);
        quote::ToTokens::to_tokens(&quote! { ( #(#types),* ) }, tokens);
    }
}

impl quote::ToTokens for WithGenerics {
    fn to_tokens(&self, tokens: &mut TokenStream) {
        let typ = &self.typ;
        let par = self.params.iter().map(|p| &p.value);
        quote::ToTokens::to_tokens(&quote! { #typ < #( #par ),* > }, tokens)
    }
}

impl quote::ToTokens for GenericParam {
    fn to_tokens(&self, tokens: &mut TokenStream) {
        match self {
            GenericParam::Type(typ) => quote::ToTokens::to_tokens(&typ, tokens),
            GenericParam::Lifetime(lifetime) => {
                unimplemented!();
            }
        }
    }
}

#[proc_macro_attribute]
pub fn autodaemon(
    attr: proc_macro::TokenStream,
    item: proc_macro::TokenStream,
) -> proc_macro::TokenStream {
    // let wat = TokenStream::from(attr)
    //     .to_token_iter()
    //     .parse::<Attrs>()
    //     .expect("parse macro attributes");

    let item_clone = item.clone();

    let token_stream = TokenStream::from(item);
    let mut i = token_stream.to_token_iter();
    let b = i.parse::<ImplBlock>().unwrap();

    let query_variants = b.body.content.iter().map(|fun| {
        let fun_name = &fun.name;
        let ret_type = &fun.ret.second;
        let fun_params = fun
            .params
            .content
            .params
            .iter()
            .filter_map(|p| p.value.to_named_param());

        quote! {
            #[allow(non_camel_case_types)]
            #fun_name {
                #( #fun_params , )*
                __s: kanal::AsyncSender< #ret_type > ,
            },
        }
    });
    let query_def = quote! {
        #[derive(Debug)]
        pub enum Query {
            #( #query_variants )*
        }
    };

    let client_funcs = b.body.content.iter().map(|fun| {
        let fun_name = &fun.name;
        let ret_type = &fun.ret.second;
        let fun_params = fun
            .params
            .content
            .params
            .iter()
            .filter_map(|p| p.value.to_named_param())
            .collect::<Vec<_>>();
        let fun_param_idents = fun_params.iter().map(|p| &p.ident);

        quote! {
            pub async fn #fun_name (&self, #( #fun_params ),* )
                -> Option<kanal::AsyncReceiver< #ret_type >>
            {
                let (__s, __r) = kanal::bounded_async(1);
                let __query = Query::#fun_name { #( #fun_param_idents, )* __s };
                self.s.send(__query).await.ok().map(move |_| __r)
            }
        }
    });
    let client_def = quote! {
        #[derive(Debug, Clone)]
        pub struct Client { pub s: kanal::AsyncSender< Query > }

        impl Client {
            #( #client_funcs )*
        }
    };

    let typ_name = b.typ_name;
    let dispatch_arms = b.body.content.iter().map(|fun| {
        let fun_name = &fun.name;
        let fun_param_idents = fun
            .params
            .content
            .params
            .iter()
            .filter_map(|p| p.value.to_named_param())
            .map(|p| &p.ident).collect::<Box<[_]>>();

        quote! {
            Query::#fun_name { __s, .. } if __s.is_closed() => {
                false
            },
            Query::#fun_name { #( #fun_param_idents, )* __s } => {
                __s.try_send(self.#fun_name(#( #fun_param_idents, )*)).is_ok()
            },
        }
    });
    let dispatch_def = quote! {
        impl #typ_name {
            fn dispatch(&mut self, q: Query) -> bool {
                match q {
                    #( #dispatch_arms )*
                }
            }
        }
    };

    // eprintln!("{}", query_def);
    // eprintln!("{}", client_def);
    // eprintln!("{}", dispatch_def);

    let mut output = TokenStream::new();

    output.extend(TokenStream::from(item_clone));

    query_def.to_tokens(&mut output);
    client_def.to_tokens(&mut output);
    dispatch_def.to_tokens(&mut output);

    output.into()
}
